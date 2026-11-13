"""Request rate limits per person, per tenant and per client address (docs/multi-tenancy.md).

Expensive endpoints (model calls, embeddings, file processing, connector syncs, MCP) count
requests in fixed windows in Valkey. A person over their own limit is refused without using
up their tenant's; a tenant over its limit is refused for everyone in it, and no other tenant
notices: every counter lives under its tenant's key prefix (`t:<tenant_id>:rl:...`). Sign-in
is counted per client address, before any tenant is known (`rl:login:<address>:...`).

A refusal is 429 `rate_limited` with Retry-After (seconds to the end of the window). Fixed
windows can let up to twice a limit through around a window boundary; budgets (docs/usage.md)
bound what that can cost. If Valkey cannot be reached the request is let through and a warning
logged: the sessions live in Valkey too, so nothing else would work then either.

Configure with RATE_LIMITS (JSON), e.g. {"chat": {"per_user": 10}, "mcp": {"per_tenant": 0}}:
a 0 removes that limit. RATE_LIMITS_ENABLED=false turns every limit off.
"""

import math
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Final, Literal

import structlog
from redis.asyncio import Redis

from needwise.core.errors import ApiError
from needwise.core.tenancy import tenant_key

log = structlog.get_logger("needwise.ratelimit")

Scope = Literal["user", "tenant", "address"]


@dataclass(frozen=True)
class Limit:
    per_user: int = 0  # requests per window for one person; 0: no limit
    per_tenant: int = 0  # for everyone in one tenant together
    per_address: int = 0  # for one client address (sign-in only)
    window_s: int = 60
    label: str = "requests"


# Per minute unless said otherwise. Generous for people, tight for scripts.
DEFAULT_LIMITS: Final[Mapping[str, Limit]] = {
    # A question in a conversation or through /ask: retrieval plus a model answer.
    "chat": Limit(per_user=30, per_tenant=600, label="questions"),
    "search": Limit(per_user=120, per_tenant=2400, label="searches"),
    # Multi-step research: several model calls and tool calls each.
    "research": Limit(per_user=6, per_tenant=120, label="research requests"),
    # Trying an agent draft, testing a model provider, running a workflow now.
    "agent_test": Limit(per_user=10, per_tenant=100, label="test runs"),
    "analytics": Limit(per_user=20, per_tenant=400, label="analytics questions and queries"),
    # Uploads and re-indexing: parsing, chunking and embedding.
    "ingest": Limit(per_user=60, per_tenant=600, label="uploads"),
    "sync": Limit(per_user=6, per_tenant=30, label="connector syncs"),
    # MCP clients, per token owner: scripted by nature.
    "mcp": Limit(per_user=120, per_tenant=1200, label="MCP requests"),
    # Starting or finishing a sign-in, per client address (an office NAT is many people).
    "login": Limit(per_address=60, label="sign-in attempts"),
}
BUCKETS: Final = tuple(DEFAULT_LIMITS)

# Counts the person first and stops there if they are over, so one person's refused requests
# never use up the tenant's allowance. Returns 0 (allowed), 1 (person over), 2 (tenant over).
_COUNT: Final = """
local function hit(key, ttl)
  local n = redis.call('INCR', key)
  if n == 1 then redis.call('EXPIRE', key, ttl) end
  return n
end
if tonumber(ARGV[1]) > 0 and hit(KEYS[1], ARGV[3]) > tonumber(ARGV[1]) then return 1 end
if tonumber(ARGV[2]) > 0 and hit(KEYS[2], ARGV[3]) > tonumber(ARGV[2]) then return 2 end
return 0
"""


class RateLimitedError(ApiError):
    def __init__(self, bucket: str, scope: Scope, limit: Limit, allowed: int, retry_after: int):
        whom = {"user": "per person", "tenant": "for your organization", "address": "from here"}
        minutes = limit.window_s // 60
        window = "minute" if limit.window_s == 60 else f"{minutes} minutes"
        super().__init__(
            429,
            "rate_limited",
            "Too many requests",
            f"At most {allowed} {limit.label} per {window} {whom[scope]}. "
            f"Try again in {retry_after} s.",
            extra={
                "limit": {
                    "bucket": bucket,
                    "scope": scope,
                    "limit": allowed,
                    "window_s": limit.window_s,
                    "retry_after_s": retry_after,
                }
            },
            headers={"Retry-After": str(retry_after)},
        )


def merge_limits(overrides: Mapping[str, Mapping[str, int]]) -> dict[str, Limit]:
    """The defaults with RATE_LIMITS applied. Unknown buckets or fields are an error."""
    limits = dict(DEFAULT_LIMITS)
    for bucket, fields in overrides.items():
        if bucket not in limits:
            raise ValueError(f"RATE_LIMITS: unknown limit {bucket!r} (known: {', '.join(BUCKETS)})")
        unknown = set(fields) - {"per_user", "per_tenant", "per_address", "window_s"}
        if unknown:
            raise ValueError(f"RATE_LIMITS[{bucket!r}]: unknown fields {sorted(unknown)}")
        if any(value < 0 for value in fields.values()) or fields.get("window_s", 1) < 1:
            raise ValueError(f"RATE_LIMITS[{bucket!r}]: limits are >= 0, windows >= 1 s")
        limits[bucket] = replace(limits[bucket], **fields)
    return limits


class RateLimiter:
    def __init__(
        self,
        valkey: Redis,
        limits: Mapping[str, Limit],
        *,
        enabled: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.valkey = valkey
        self.limits = dict(limits)
        self.enabled = enabled
        self.clock = clock  # assignable: tests move time to the next window

    def _window(self, limit: Limit) -> tuple[int, int]:
        """(window start, seconds until the next window)."""
        now = self.clock()
        start = int(now // limit.window_s) * limit.window_s
        return start, max(1, math.ceil(start + limit.window_s - now))

    async def check(self, tenant_id: uuid.UUID, user_id: uuid.UUID, bucket: str) -> None:
        """Counts one request of this person in this tenant; RateLimitedError when over."""
        limit = self.limits[bucket]
        if not self.enabled or not (limit.per_user or limit.per_tenant):
            return
        start, retry_after = self._window(limit)
        keys = [
            tenant_key(tenant_id, "rl", bucket, "user", user_id, start),
            tenant_key(tenant_id, "rl", bucket, "all", start),
        ]
        verdict = await self._count(keys, limit.per_user, limit.per_tenant, limit.window_s)
        if verdict == 1:
            raise RateLimitedError(bucket, "user", limit, limit.per_user, retry_after)
        if verdict == 2:
            raise RateLimitedError(bucket, "tenant", limit, limit.per_tenant, retry_after)

    async def check_address(self, address: str | None, bucket: str) -> None:
        """Counts one request from a client address (before a tenant is known)."""
        limit = self.limits[bucket]
        if not self.enabled or not limit.per_address or not address:
            return
        start, retry_after = self._window(limit)
        key = f"rl:{bucket}:{address}:{start}"
        if await self._count([key, key], limit.per_address, 0, limit.window_s):
            raise RateLimitedError(bucket, "address", limit, limit.per_address, retry_after)

    async def _count(self, keys: list[str], first: int, second: int, window_s: int) -> int:
        try:
            # The key lives one window and a little more, so a slow clock never resets it early.
            result = await self.valkey.eval(_COUNT, 2, *keys, first, second, window_s + 5)  # pyright: ignore[reportUnknownMemberType, reportGeneralTypeIssues]
        except Exception:
            log.warning("rate_limit_unavailable", exc_info=True)
            return 0
        return int(result)  # pyright: ignore[reportUnknownArgumentType]
