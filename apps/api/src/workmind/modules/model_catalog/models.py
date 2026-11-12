"""A tenant's model providers (bring your own key), its catalog of models with prices, and its
role mapping and data policy (docs/models.md). The deployment's own provider ("system") is not
stored: it comes from the settings and is read-only.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workmind.core.db import Base, TenantScoped, Timestamps, uuid_primary_key

PROVIDER_KINDS = ("anthropic", "openai_compatible")
SENSITIVITY_LEVELS = ("public", "internal", "confidential", "restricted")


class ModelProvider(Base, TenantScoped, Timestamps):
    __tablename__ = "model_providers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "name"),
        CheckConstraint(f"kind IN ({', '.join(repr(k) for k in PROVIDER_KINDS)})", name="kind"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name_length"),
        CheckConstraint("kind = 'anthropic' OR base_url IS NOT NULL", name="base_url_required"),
        CheckConstraint("kind = 'openai_compatible' OR NOT local", name="anthropic_not_local"),
        CheckConstraint("(api_key IS NULL) = (key_hint IS NULL)", name="key_hint"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str | None] = mapped_column(Text)  # None: the provider's public API
    # AES-256-GCM (modules/model_catalog/secrets.py), bound to tenant, id, kind and base URL.
    api_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_hint: Mapped[str | None] = mapped_column(String(16))  # "…a1b2": all the API shows
    # An admin's statement that requests stay on infrastructure they control (the data policy
    # sends sensitive sources only to local models).
    local: Mapped[bool] = mapped_column(server_default=sa_false())
    enabled: Mapped[bool] = mapped_column(server_default=sa_true())


class CatalogModel(Base, TenantScoped, Timestamps):
    """A model of a tenant provider. Its catalog id is "<provider id>:<name>"."""

    __tablename__ = "model_catalog"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_id", "name"),
        ForeignKeyConstraint(
            ["tenant_id", "provider_id"],
            ["model_providers.tenant_id", "model_providers.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("char_length(name) BETWEEN 1 AND 200", name="name_length"),
        CheckConstraint("char_length(label) BETWEEN 1 AND 200", name="label_length"),
        CheckConstraint(
            "(input_usd_per_mtok IS NULL OR input_usd_per_mtok >= 0) AND "
            "(output_usd_per_mtok IS NULL OR output_usd_per_mtok >= 0)",
            name="prices",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    provider_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(Text)  # the model name sent to the provider
    label: Mapped[str] = mapped_column(Text)
    input_usd_per_mtok: Mapped[float | None] = mapped_column(Double)
    output_usd_per_mtok: Mapped[float | None] = mapped_column(Double)
    enabled: Mapped[bool] = mapped_column(server_default=sa_true())


class ModelSettings(Base, TenantScoped):
    """One per tenant, created on the first change. `roles` and `policy` hold only what the
    tenant chose; anything missing follows the deployment's defaults. `version` goes up on
    every change to the tenant's providers, catalog, roles or policy: other processes compare
    it to drop their cached resolution."""

    __tablename__ = "model_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (updated_by)",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    # role -> catalog model id, e.g. {"judge": "system:claude-opus-5-5"}
    roles: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    # {"max_external_sensitivity": "confidential", "local_model": "<catalog id>" | null}
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    version: Mapped[int] = mapped_column(BigInteger, server_default="0")
    updated_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
