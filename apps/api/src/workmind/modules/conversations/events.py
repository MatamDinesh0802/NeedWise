"""Run events in a Valkey Stream (ARCHITECTURE section 18).

    POST /conversations/{id}/messages -> 202 {run_id}
    GET  /runs/{run_id}/events        -> SSE, replayed from the stream

The stream outlives the HTTP connection: a closed tab, a reconnect (`Last-Event-ID`) or a second
device sees the same events. Streams expire an hour after the run's last event.

Every key lives under the run's tenant (`t:<tenant_id>:run:<run_id>...`): a run is only ever
looked up within the asking person's tenant, so another tenant's run id finds nothing.
"""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import TimeoutError as RedisTimeoutError

from workmind.core.tenancy import tenant_key

TTL_SECONDS = 3600
# How long one XREAD waits for new events. Must stay below the client's socket timeout
# (redis-py defaults to 5 s), or a pause between events breaks the response mid-stream.
BLOCK_MS = 3000
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


def _key(tenant_id: uuid.UUID, run_id: uuid.UUID, *suffix: str) -> str:
    return tenant_key(tenant_id, "run", run_id, *suffix)


def conversation_lock(tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    """Holds the id of the conversation's run in progress (one answer at a time)."""
    return tenant_key(tenant_id, "conversation", conversation_id, "run")


class RunEvents:
    def __init__(self, valkey: Redis) -> None:
        self._valkey = valkey

    async def create(self, meta: RunMeta) -> bool:
        """Registers a run. False if the conversation already has a run in progress."""
        lock = conversation_lock(meta.tenant_id, meta.conversation_id)
        if not await self._valkey.set(lock, str(meta.run_id), nx=True, ex=TTL_SECONDS):
            return False
        key = _key(meta.tenant_id, meta.run_id)
        await self._valkey.hset(  # pyright: ignore[reportUnknownMemberType]
            key,
            mapping={
                "tenant_id": str(meta.tenant_id),
                "user_id": str(meta.user_id),
                "conversation_id": str(meta.conversation_id),
            },
        )
        await self._valkey.expire(key, TTL_SECONDS)
        return True

    async def meta(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> RunMeta | None:
        """The run, if it exists in this tenant (another tenant's run id finds nothing)."""
        key = _key(tenant_id, run_id)
        raw = cast(dict[bytes, bytes], await self._valkey.hgetall(key))  # pyright: ignore[reportUnknownMemberType]
        if not raw:
            return None
        fields = {k.decode(): v.decode() for k, v in raw.items()}
        return RunMeta(
            run_id=run_id,
            tenant_id=uuid.UUID(fields["tenant_id"]),
            user_id=uuid.UUID(fields["user_id"]),
            conversation_id=uuid.UUID(fields["conversation_id"]),
        )

    async def active_run(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> uuid.UUID | None:
        lock = conversation_lock(tenant_id, conversation_id)
        raw = cast(bytes | None, await self._valkey.get(lock))
        return uuid.UUID(raw.decode()) if raw else None

    async def emit(self, run: RunMeta, event: str, data: dict[str, Any]) -> None:
        stream = _key(run.tenant_id, run.run_id, "events")
        await self._valkey.xadd(  # pyright: ignore[reportUnknownMemberType]
            stream, {"event": event, "data": json.dumps(data)}, maxlen=MAX_EVENTS, approximate=True
        )
        await self._valkey.expire(stream, TTL_SECONDS)
        if event in TERMINAL:
            lock = conversation_lock(run.tenant_id, run.conversation_id)
            if await self._valkey.get(lock) == str(run.run_id).encode():
                await self._valkey.delete(lock)

    async def cancel(self, run: RunMeta) -> None:
        await self._valkey.set(_key(run.tenant_id, run.run_id, "cancel"), "1", ex=TTL_SECONDS)

    async def cancelled(self, run: RunMeta) -> bool:
        return bool(await self._valkey.exists(_key(run.tenant_id, run.run_id, "cancel")))

    async def read(
        self, run: RunMeta, *, after: str = "0", block_ms: int = BLOCK_MS
    ) -> AsyncIterator[StoredEvent | None]:
        """Yields events after `after`, then waits for new ones. Yields None when nothing arrived
        for `block_ms` (the caller sends a keep-alive). Stops after a terminal event."""
        stream = _key(run.tenant_id, run.run_id, "events")
        last = after
        while True:
            try:
                response = cast(
                    list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]],
                    await self._valkey.xread({stream: last}, count=200, block=block_ms),  # pyright: ignore[reportUnknownMemberType]
                )
            except (TimeoutError, RedisTimeoutError):
                response = []  # a quiet stretch is not an error: send a keep-alive
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
