"""Which sources are due for a sync. Runs every minute on Celery beat (one scheduler process)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from needwise.core.tenancy import global_transaction, tenant_transaction
from needwise.modules.connectors.models import Source
from needwise.modules.identity.models import Tenant


async def due_sources(
    sessions: async_sessionmaker[AsyncSession], now: datetime | None = None
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    now = now or datetime.now(UTC)
    async with global_transaction(sessions) as session:
        tenants = (await session.scalars(select(Tenant.id).where(Tenant.status == "active"))).all()
    due: list[tuple[uuid.UUID, uuid.UUID]] = []
    for tenant_id in tenants:  # Row-Level Security: one tenant at a time
        async with tenant_transaction(sessions, tenant_id) as session:
            for source in await session.scalars(
                select(Source).where(Source.enabled.is_(True), Source.status != "syncing")
            ):
                last = source.last_sync_at
                if last is None or last + timedelta(minutes=source.interval_minutes) <= now:
                    due.append((tenant_id, source.id))
    return due
