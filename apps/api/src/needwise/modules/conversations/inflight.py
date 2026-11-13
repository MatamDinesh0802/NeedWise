"""How much model work one person may have running at once (docs/security/owasp-llm-top10.md,
LLM10: unbounded consumption).

Budgets stop spending in USD, but only for models with a listed price: a local model costs
nothing in usage, so before this a script holding one session or access token could start an
answer in each of a thousand conversations at the same time. Every model-backed request now
takes a slot first: a chat or research run holds it until the run ends; /assistant/ask,
/assistant/research, /analytics/ask, MCP `ask` and the agent test console while they answer.
A person who already holds `limit` slots gets 429 too_many_requests.

Slots are members of one sorted set per person in Valkey (`t:<tenant>:inflight:<user>`), shared
by every API process, scored by when they were taken. A slot older than `ttl_s` no longer counts,
so a process that dies holding one does not lock its owner out.
"""

import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from redis.asyncio import Redis

from needwise.core.context import RequestContext
from needwise.core.errors import ApiError
from needwise.core.tenancy import tenant_key

DEFAULT_TTL_S = 900


class InFlight:
    def __init__(self, valkey: Redis, limit: int, *, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._valkey = valkey
        self.limit = limit
        self.ttl_s = ttl_s

    @staticmethod
    def key(ctx: RequestContext) -> str:
        return tenant_key(ctx.tenant_id, "inflight", ctx.user_id)

    async def acquire(self, ctx: RequestContext, token: str) -> bool:
        """Takes a slot named `token`. False (and nothing taken) when the person is at the limit."""
        key, now = self.key(ctx), time.time()
        async with self._valkey.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - self.ttl_s)  # slots of dead processes
            pipe.zadd(key, {token: now})
            pipe.zcard(key)
            pipe.expire(key, self.ttl_s)
            _, _, held, _ = await pipe.execute()
        if int(held) > self.limit:
            await self._valkey.zrem(key, token)
            return False
        return True

    async def release(self, ctx: RequestContext, token: str) -> None:
        await self._valkey.zrem(self.key(ctx), token)

    def refusal(self) -> ApiError:
        return ApiError(
            429,
            "too_many_requests",
            "Too many answers in progress",
            f"You already have {self.limit} answers in progress. Wait for one to finish.",
        )

    @asynccontextmanager
    async def hold(self, ctx: RequestContext) -> AsyncGenerator[None]:
        """A slot for the duration of the block, or ApiError 429."""
        token = uuid.uuid4().hex
        if not await self.acquire(ctx, token):
            raise self.refusal()
        try:
            yield
        finally:
            await self.release(ctx, token)
