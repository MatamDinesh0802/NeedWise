"""Tenant-scoped database transactions.

Row-Level Security policies compare each row's tenant_id with the `app.tenant_id` setting.
It is set with `set_config(..., is_local => true)`, so it lasts exactly one transaction and
can never leak to the next request that reuses the pooled connection.
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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
