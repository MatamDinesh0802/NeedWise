"""Long-lived clients shared by the whole process, created at startup and closed at shutdown."""

from dataclasses import dataclass
from typing import Self

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from workmind.core.config import Settings
from workmind.core.db import create_engine, create_session_factory
from workmind.modules.authz.openfga import OpenFgaClient
from workmind.modules.identity.oidc import OidcClient
from workmind.modules.identity.sessions import SessionStore


@dataclass
class Resources:
    engine: AsyncEngine
    db: async_sessionmaker[AsyncSession]
    valkey: Redis
    http: httpx.AsyncClient
    oidc: OidcClient
    sessions: SessionStore
    fga: OpenFgaClient

    @classmethod
    def create(cls, settings: Settings) -> Self:
        # Every client connects lazily, so startup never blocks on infrastructure.
        engine = create_engine(settings.database_url)
        valkey = Redis.from_url(settings.valkey_url)  # pyright: ignore[reportUnknownMemberType] (untyped **kwargs upstream)
        http = httpx.AsyncClient(timeout=10)
        return cls(
            engine=engine,
            db=create_session_factory(engine),
            valkey=valkey,
            http=http,
            oidc=OidcClient(
                http,
                discovery_url=settings.oidc_discovery_url,
                client_id=settings.oidc_client_id,
                client_secret=settings.oidc_client_secret.get_secret_value(),
                redirect_uri=settings.oidc_redirect_uri,
            ),
            sessions=SessionStore(
                valkey,
                idle_timeout_s=settings.session_idle_timeout_s,
                absolute_timeout_s=settings.session_absolute_timeout_s,
            ),
            fga=OpenFgaClient(
                http, settings.openfga_api_url, settings.openfga_api_key.get_secret_value()
            ),
        )

    async def aclose(self) -> None:
        await self.http.aclose()
        await self.valkey.aclose()
        await self.engine.dispose()
