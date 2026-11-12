"""How many of one person's analytics queries run at once, counted in Valkey (docs/analytics.md).

The API runs several processes (API_WORKERS, Helm `api.workers`) and pods, each with its own
QueryPool. Counted per process, "one query per person at a time" became one per process. Here
each running query holds a lease in a sorted set under the person's tenant prefix
(`t:<tenant_id>:analytics:running:<user_id>`), scored by when it expires, so the count holds
across every process and a process that dies cannot keep a slot past its lease.

If Valkey cannot be reached, slots are counted in this process (and a warning logged): the
per-process bound still holds, and the sessions live in Valkey too, so little else works then.
"""

import uuid
from typing import Final

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from workmind.modules.analytics.engine import LocalSlots

log = structlog.get_logger("workmind.analytics")

# KEYS[1]: the person's leases; ARGV: limit, lease lifetime (ms), lease id. Valkey's clock, so
# processes on hosts with different clocks agree.
_TAKE: Final = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
  return 0
end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[3])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]))
return 1
"""
_LOCAL = "local:"


class ValkeySlots:
    def __init__(self, valkey: Redis) -> None:
        self._valkey = valkey
        self._local = LocalSlots()

    async def take(self, owner: str, limit: int, hold_s: float) -> str | None:
        lease = uuid.uuid4().hex
        try:
            taken = await self._valkey.eval(_TAKE, 1, owner, limit, int(hold_s * 1000), lease)  # pyright: ignore[reportUnknownMemberType, reportGeneralTypeIssues]
        except RedisError as exc:
            log.warning("analytics_slots_unavailable", error=repr(exc))
            local = await self._local.take(owner, limit, hold_s)
            return _LOCAL + local if local is not None else None
        return lease if taken == 1 else None

    async def give_back(self, owner: str, lease: str) -> None:
        if lease.startswith(_LOCAL):
            await self._local.give_back(owner, lease.removeprefix(_LOCAL))
            return
        try:
            await self._valkey.zrem(owner, lease)  # pyright: ignore[reportUnknownMemberType]
        except RedisError as exc:  # the lease expires on its own
            log.warning("analytics_slots_unavailable", error=repr(exc))
