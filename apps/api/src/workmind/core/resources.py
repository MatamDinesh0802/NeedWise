"""Long-lived clients shared by the whole process, created at startup and closed at shutdown."""

from dataclasses import dataclass
from typing import Self

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from workmind.core.config import Settings
from workmind.core.db import create_engine


@dataclass
class Resources:
    engine: AsyncEngine
    valkey: Redis
    http: httpx.AsyncClient

    @classmethod
    def create(cls, settings: Settings) -> Self:
        # All three clients connect lazily, so startup never blocks on infrastructure.
        return cls(
            engine=create_engine(settings.database_url),
            valkey=Redis.from_url(settings.valkey_url),  # pyright: ignore[reportUnknownMemberType] (untyped **kwargs upstream)
            http=httpx.AsyncClient(timeout=settings.health_check_timeout_s),
        )

    async def aclose(self) -> None:
        await self.http.aclose()
        await self.valkey.aclose()
        await self.engine.dispose()
