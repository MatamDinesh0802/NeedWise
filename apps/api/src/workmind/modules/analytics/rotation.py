"""Re-encrypting stored SQL source passwords with the active key (`wm secrets rotate`, next to
model provider keys; docs/analytics-sql-sources.md#the-password).

Run it after making a new key active (SECRETS_ENCRYPTION_KEY) with the old one still listed in
SECRETS_ENCRYPTION_PREVIOUS_KEYS. Once it reports no failures, the old key can be removed.
"""

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.tenancy import global_transaction, tenant_transaction
from workmind.modules.analytics.models import AnalyticsSource
from workmind.modules.analytics.sources import password_context
from workmind.modules.identity.models import Tenant
from workmind.modules.model_catalog.rotation import RotationReport
from workmind.modules.model_catalog.secrets import KeyRing, SecretsError

log = structlog.get_logger("workmind.analytics")


async def rotate_source_passwords(
    sessions: async_sessionmaker[AsyncSession], keyring: KeyRing
) -> RotationReport:
    report = RotationReport()
    async with global_transaction(sessions) as session:
        tenant_ids = list((await session.scalars(select(Tenant.id))).all())
    for tenant_id in tenant_ids:
        async with tenant_transaction(sessions, tenant_id) as session:
            rows = (
                await session.scalars(
                    select(AnalyticsSource)
                    .where(AnalyticsSource.password.is_not(None))
                    .with_for_update()
                )
            ).all()
            for row in rows:
                if row.password is None:
                    continue
                if not keyring.needs_rotation(row.password):
                    report.current += 1
                    continue
                context = password_context(
                    tenant_id, row.id, row.kind, row.host, row.port, row.database, row.username
                )
                try:
                    row.password = keyring.rotate(row.password, context=context)
                except SecretsError as exc:
                    report.failed += 1
                    log.warning(
                        "analytics_source_password_not_rotated",
                        tenant_id=str(tenant_id),
                        source=row.key,
                        reason=str(exc),
                    )
                    continue
                report.rotated += 1
    return report
