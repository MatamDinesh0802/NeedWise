"""Tenant scoping for every store (ARCHITECTURE section 8, docs/multi-tenancy.md).

PostgreSQL: Row-Level Security policies compare each row's tenant_id with the `app.tenant_id`
setting. It is set with `set_config(..., is_local => true)`, so it lasts exactly one transaction
and can never leak to the next request that reuses the pooled connection.

Valkey: every key that belongs to a tenant starts with `t:<tenant_id>:` (`tenant_key`). Deleting
a tenant removes everything under that prefix. The few keys written before a tenant is known
(sign-in flows, the session lookup by cookie) are listed in GLOBAL_KEY_PREFIXES; an isolation
test scans Valkey and fails on any key that is neither.
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

TENANT_KEY_PREFIX = "t:"
# Keys that cannot carry a tenant: they are written before the tenant is known, or looked up by
# a secret the caller holds (a session cookie, an OAuth state) to find the tenant.
GLOBAL_KEY_PREFIXES = (
    "session:",  # session data by the SHA-256 of its cookie (it names the tenant inside)
    "session-index:",  # Keycloak session / subject -> sessions, for back-channel logout
    "oidc:flow:",  # an unfinished sign-in (state, nonce, PKCE verifier), 10 minutes
    "rl:login:",  # sign-in rate limits by client address
)


def tenant_key(tenant_id: uuid.UUID, *parts: object) -> str:
    """A Valkey key owned by one tenant: t:<tenant_id>:<part>:<part>..."""
    return ":".join([f"{TENANT_KEY_PREFIX}{tenant_id}", *(str(part) for part in parts)])


def tenant_key_pattern(tenant_id: uuid.UUID) -> str:
    """SCAN pattern for every key of one tenant."""
    return f"{TENANT_KEY_PREFIX}{tenant_id}:*"


def key_tenant(key: str) -> uuid.UUID | None:
    """The tenant a key belongs to, or None for a key outside every tenant's prefix."""
    if not key.startswith(TENANT_KEY_PREFIX):
        return None
    try:
        return uuid.UUID(key[len(TENANT_KEY_PREFIX) :].split(":", 1)[0])
    except ValueError:
        return None


@asynccontextmanager
async def tenant_transaction(
    session_factory: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
) -> AsyncGenerator[AsyncSession, None]:
    """Open a transaction in which every query is restricted to one tenant."""
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        yield session


@asynccontextmanager
async def global_transaction(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """A transaction with no tenant: tenant-scoped tables return no rows (fail closed).

    Only for global lookups such as resolving a tenant by slug during login.
    """
    async with session_factory() as session, session.begin():
        yield session
