"""Re-encrypting stored provider keys with the active key (`wm secrets rotate`, docs/models.md).

Run it after making a new key active (SECRETS_ENCRYPTION_KEY) with the old one still listed in
SECRETS_ENCRYPTION_PREVIOUS_KEYS. Once it reports no failures, the old key can be removed.
"""

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workmind.core.tenancy import global_transaction, tenant_transaction
from workmind.modules.identity.models import Tenant
from workmind.modules.model_catalog.models import ModelProvider
from workmind.modules.model_catalog.secrets import KeyRing, SecretsError
from workmind.modules.model_catalog.store import key_context

log = structlog.get_logger("workmind.models")


@dataclass
class RotationReport:
    rotated: int = 0  # re-encrypted with the active key
    current: int = 0  # already on the active key
    failed: int = 0  # cannot be decrypted with any configured key: re-enter them


async def rotate_keys(
    sessions: async_sessionmaker[AsyncSession], keyring: KeyRing
) -> RotationReport:
    report = RotationReport()
    async with global_transaction(sessions) as session:
        tenant_ids = list((await session.scalars(select(Tenant.id))).all())
    for tenant_id in tenant_ids:
        async with tenant_transaction(sessions, tenant_id) as session:
            rows = (
                await session.scalars(
                    select(ModelProvider)
                    .where(ModelProvider.api_key.is_not(None))
                    .with_for_update()
                )
            ).all()
            for row in rows:
                assert row.api_key is not None  # noqa: S101  (filtered above)
                if not keyring.needs_rotation(row.api_key):
                    report.current += 1
                    continue
                context = key_context(tenant_id, row.id, row.kind, row.base_url)
                try:
                    row.api_key = keyring.rotate(row.api_key, context=context)
                except SecretsError as exc:
                    report.failed += 1
                    log.warning(
                        "model_provider_key_not_rotated",
                        tenant_id=str(tenant_id),
                        provider_id=str(row.id),
                        reason=str(exc),
                    )
                    continue
                report.rotated += 1
    return report
