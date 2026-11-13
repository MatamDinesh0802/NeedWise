"""Reading a tenant's model configuration from the database (inside its tenant transaction)."""

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from needwise.modules.identity.models import Tenant
from needwise.modules.model_catalog.catalog import ModelInfo, ProviderInfo, TenantConfig, catalog_id
from needwise.modules.model_catalog.models import CatalogModel, ModelProvider, ModelSettings
from needwise.modules.model_catalog.secrets import KeyRing, SecretsError

log = structlog.get_logger("needwise.models")


def key_context(
    tenant_id: uuid.UUID, provider_id: uuid.UUID, kind: str, base_url: str | None
) -> bytes:
    """What a stored key is bound to: it decrypts only for this tenant, provider, kind and URL."""
    return f"model-provider-key:v1:{tenant_id}:{provider_id}:{kind}:{base_url or ''}".encode()


def key_hint(api_key: str) -> str:
    """The last four characters, for keys long enough that this gives nothing away."""
    return "…" + api_key[-4:] if len(api_key) >= 12 else "…"


def stamp_of(version: int | None, tenant_settings: dict[str, Any] | None) -> tuple[int, str | None]:
    restricted = (tenant_settings or {}).get("restricted_data")
    return (version or 0, restricted if isinstance(restricted, str) else None)


async def read_stamp(session: AsyncSession, tenant_id: uuid.UUID) -> tuple[int, str | None]:
    version = await session.scalar(select(ModelSettings.version))
    tenant_settings = await session.scalar(select(Tenant.settings).where(Tenant.id == tenant_id))
    return stamp_of(version, tenant_settings)


def provider_info(row: ModelProvider) -> ProviderInfo:
    return ProviderInfo(
        id=str(row.id),
        name=row.name,
        kind=row.kind,
        base_url=row.base_url,
        local=row.local,
        has_key=row.api_key is not None,
        key_hint=row.key_hint,
        enabled=row.enabled,
        system=False,
    )


def model_info(row: CatalogModel, provider: ProviderInfo) -> ModelInfo:
    return ModelInfo(
        id=catalog_id(str(row.provider_id), row.name),
        provider_id=str(row.provider_id),
        name=row.name,
        label=row.label,
        local=not provider.external,
        input_usd_per_mtok=row.input_usd_per_mtok,
        output_usd_per_mtok=row.output_usd_per_mtok,
        enabled=row.enabled,
    )


async def load_config(
    session: AsyncSession, tenant_id: uuid.UUID, keyring: KeyRing | None = None
) -> TenantConfig:
    """The tenant's providers, models, roles and policy. With `keyring`, keys are decrypted
    (only the resolver and the provider test need them); a key that fails is reported in
    `key_errors`, never raised."""
    stored = await session.scalar(select(ModelSettings))
    tenant_settings = await session.scalar(select(Tenant.settings).where(Tenant.id == tenant_id))
    providers = (
        await session.scalars(select(ModelProvider).order_by(ModelProvider.created_at))
    ).all()
    models = (await session.scalars(select(CatalogModel).order_by(CatalogModel.created_at))).all()
    config = TenantConfig(
        stamp=stamp_of(stored.version if stored else None, tenant_settings),
        roles=dict(stored.roles) if stored else {},
        policy=dict(stored.policy) if stored else {},
        tenant_settings=dict(tenant_settings or {}),
    )
    for row in providers:
        info = provider_info(row)
        config.providers[info.id] = info
        if row.api_key is None or keyring is None:
            continue
        try:
            config.keys[info.id] = keyring.open(
                row.api_key, context=key_context(tenant_id, row.id, row.kind, row.base_url)
            )
        except SecretsError as exc:
            config.key_errors[info.id] = str(exc)
            log.warning(
                "model_provider_key_unreadable",
                tenant_id=str(tenant_id),
                provider_id=info.id,
                reason=str(exc),
            )
    for row in models:
        info = model_info(row, config.providers[str(row.provider_id)])
        config.models[info.id] = info
    return config
