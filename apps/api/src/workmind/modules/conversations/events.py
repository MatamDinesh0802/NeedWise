"""Run events in a Valkey Stream (ARCHITECTURE section 18).

    POST /conversations/{id}/messages -> 202 {run_id}
    GET  /runs/{run_id}/events        -> SSE, replayed from the stream

The stream outlives the HTTP connection: a closed tab, a reconnect (`Last-Event-ID`) or a second
device sees the same events. Streams expire an hour after the run's last event.
"""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

from redis.asyncio import Redis

TTL_SECONDS = 3600
MAX_EVENTS = 20_000
TERMINAL = frozenset({"run.completed", "run.failed", "run.cancelled"})


@dataclass(frozen=True)
class RunMeta:
    run_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    conversation_id: uuid.UUID


@dataclass(frozen=True)
class StoredEvent:
    id: str  # the stream entry id, used as the SSE id
    event: str
    data: dict[str, Any]

    def sse(self) -> str:
        return f"id: {self.id}\nevent: {self.event}\ndata: {json.dumps(self.data)}\n\n"


def _key(run_id: uuid.UUID, suffix: str = "") -> str:
    return f"wm:run:{run_id}{suffix}"


class RunEvents:
    def __init__(self, valkey: Redis) -> None:
        self._valkey = valkey

    async def create(self, meta: RunMeta) -> bool:
        """Registers a run. False if the conversation already has a run in progress."""
        lock = f"wm:conversation:{meta.conversation_id}:run"
        if not await self._valkey.set(lock, str(meta.run_id), nx=True, ex=TTL_SECONDS):
            return False
        await self._valkey.hset(  # pyright: ignore[reportUnknownMemberType]
            _key(meta.run_id),
            mapping={
                "tenant_id": str(meta.tenant_id),
                "user_id": str(meta.user_id),
                "conversation_id": str(meta.conversation_id),
            },
        )
        await self._valkey.expire(_key(meta.run_id), TTL_SECONDS)
        return True

    async def meta(self, run_id: uuid.UUID) -> RunMeta | None:
        raw = cast(dict[bytes, bytes], await self._valkey.hgetall(_key(run_id)))  # pyright: ignore[reportUnknownMemberType]
        if not raw:
            return None
        fields = {k.decode(): v.decode() for k, v in raw.items()}
        return RunMeta(
            run_id=run_id,
            tenant_id=uuid.UUID(fields["tenant_id"]),
            user_id=uuid.UUID(fields["user_id"]),
            conversation_id=uuid.UUID(fields["conversation_id"]),
        )

    async def emit(self, run_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
        stream = _key(run_id, ":events")
        await self._valkey.xadd(  # pyright: ignore[reportUnknownMemberType]
            stream, {"event": event, "data": json.dumps(data)}, maxlen=MAX_EVENTS, approximate=True
        )
        await self._valkey.expire(stream, TTL_SECONDS)
        if event in TERMINAL:
            meta = await self.meta(run_id)
            if meta:
                lock = f"wm:conversation:{meta.conversation_id}:run"
                if await self._valkey.get(lock) == str(run_id).encode():
                    await self._valkey.delete(lock)

    async def cancel(self, run_id: uuid.UUID) -> None:
        await self._valkey.set(_key(run_id, ":cancel"), "1", ex=TTL_SECONDS)

    async def cancelled(self, run_id: uuid.UUID) -> bool:
        return bool(await self._valkey.exists(_key(run_id, ":cancel")))

    async def read(
        self, run_id: uuid.UUID, *, after: str = "0", block_ms: int = 15_000
    ) -> AsyncIterator[StoredEvent | None]:
        """Yields events after `after`, then waits for new ones. Yields None when nothing arrived
        for `block_ms` (the caller sends a keep-alive). Stops after a terminal event."""
        stream = _key(run_id, ":events")
        last = after
        while True:
            response = cast(
                list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]],
                await self._valkey.xread({stream: last}, count=200, block=block_ms),  # pyright: ignore[reportUnknownMemberType]
            )
            if not response:
                yield None
                continue
            for entry_id, fields in response[0][1]:
                last = entry_id.decode()
                event = StoredEvent(
                    id=last,
                    event=fields[b"event"].decode(),
                    data=json.loads(fields[b"data"]),
                )
                yield event
                if event.event in TERMINAL:
                    return
