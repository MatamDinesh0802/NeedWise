"""Server-side sessions in Valkey (ADR 0003).

The browser holds only a random session ID. Valkey stores data under the ID's SHA-256, so a
dump of Valkey yields no usable cookies. Sessions are indexed by Keycloak session and subject,
so Keycloak's back-channel logout can end them.
"""

import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace

from redis.asyncio import Redis

_MAX_SESSION_ID_LENGTH = 128


@dataclass(frozen=True)
class SessionData:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    keycloak_sub: str
    keycloak_sid: str | None
    id_token: str
    created_at: float
    last_seen: float
    expires_at: float

    def to_json(self) -> str:
        data = asdict(self)
        data["user_id"], data["tenant_id"] = str(self.user_id), str(self.tenant_id)
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "SessionData":
        data = json.loads(raw)
        data["user_id"], data["tenant_id"] = (
            uuid.UUID(data["user_id"]),
            uuid.UUID(data["tenant_id"]),
        )
        return cls(**data)


def _key(session_id: str) -> str:
    return "session:" + hashlib.sha256(session_id.encode()).hexdigest()


def _by_sid(sid: str) -> str:
    return f"session-index:sid:{sid}"


def _by_sub(sub: str) -> str:
    return f"session-index:sub:{sub}"


class SessionStore:
    def __init__(
        self,
        valkey: Redis,
        *,
        idle_timeout_s: int,
        absolute_timeout_s: int,
        touch_interval_s: int = 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._valkey = valkey
        self._idle = idle_timeout_s
        self._absolute = absolute_timeout_s
        self._touch_interval = touch_interval_s
        self._clock = clock

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        keycloak_sub: str,
        keycloak_sid: str | None,
        id_token: str,
    ) -> str:
        session_id = secrets.token_urlsafe(32)
        now = self._clock()
        data = SessionData(
            user_id=user_id,
            tenant_id=tenant_id,
            keycloak_sub=keycloak_sub,
            keycloak_sid=keycloak_sid,
            id_token=id_token,
            created_at=now,
            last_seen=now,
            expires_at=now + self._absolute,
        )
        key = _key(session_id)
        indexes = [_by_sub(keycloak_sub)] + ([_by_sid(keycloak_sid)] if keycloak_sid else [])
        async with self._valkey.pipeline(transaction=True) as pipe:
            pipe.set(key, data.to_json(), ex=self._idle)
            for index in indexes:
                pipe.sadd(index, key)
                pipe.expire(index, self._absolute)
            await pipe.execute()
        return session_id

    async def get(self, session_id: str) -> SessionData | None:
        if not session_id or len(session_id) > _MAX_SESSION_ID_LENGTH:
            return None
        key = _key(session_id)
        raw = await self._valkey.get(key)
        if raw is None:
            return None  # never existed, logged out, or idle timeout (key TTL)
        data = SessionData.from_json(raw)
        now = self._clock()
        if now >= data.expires_at:
            await self._valkey.delete(key)
            return None
        if now - data.last_seen >= self._touch_interval:
            data = replace(data, last_seen=now)
            ttl = max(1, int(min(self._idle, data.expires_at - now)))
            await self._valkey.set(key, data.to_json(), ex=ttl)
        return data

    async def delete(self, session_id: str) -> None:
        await self._valkey.delete(_key(session_id))

    async def delete_by_keycloak_session(self, sid: str) -> int:
        return await self._delete_index(_by_sid(sid))

    async def delete_by_keycloak_subject(self, sub: str) -> int:
        return await self._delete_index(_by_sub(sub))

    async def _delete_index(self, index: str) -> int:
        keys: set[bytes | str] = await self._valkey.smembers(index)  # pyright: ignore[reportUnknownMemberType, reportGeneralTypeIssues]
        removed = int(await self._valkey.delete(*keys)) if keys else 0
        await self._valkey.delete(index)
        return removed
