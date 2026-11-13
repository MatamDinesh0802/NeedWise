"""Business intelligence (ARCHITECTURE section 16): each tenant's semantic model and dataset,
its PostgreSQL and MySQL sources, and the questions people asked the BI agent."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from needwise.core.db import Base, TenantScoped, Timestamps, uuid_primary_key
from needwise.modules.identity.models import SLUG_PATTERN

RUN_STATUSES = ("running", "done", "failed")
SOURCE_KINDS = ("postgres", "mysql")
TLS_MODES = ("disable", "require", "verify-full")


class AnalyticsModel(Base, TenantScoped, Timestamps):
    """One per tenant: the semantic model (YAML) and the current version of the dataset.

    The dataset is a DuckDB file at <object storage>/analytics/<tenant_id>/<version>.duckdb.
    Rebuilding it writes a new version file and then switches `dataset_version` here, in one
    transaction, so a reader always opens a complete file."""

    __tablename__ = "analytics_models"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (updated_by)",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    yaml: Mapped[str | None] = mapped_column(Text)  # None until an admin sets a model
    dataset_version: Mapped[str | None] = mapped_column(String(64))  # None: no data loaded
    # table -> {"rows", "columns": [{"name", "type"}], "source", "sha256", "loaded_at"}
    dataset_tables: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    updated_by: Mapped[uuid.UUID | None]


class AnalyticsSource(Base, TenantScoped, Timestamps):
    """A PostgreSQL or MySQL database an admin connected (docs/analytics-sql-sources.md), with
    its own semantic model: a dataset next to the uploaded files, with its own access list and
    row policies.

    The password is AES-256-GCM ciphertext (SECRETS_ENCRYPTION_KEY, bound to the tenant, the
    source and where it connects: sources.password_context) and is never returned by the API."""

    __tablename__ = "analytics_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "key"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (created_by)",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (updated_by)",
        ),
        CheckConstraint(f"key ~ '{SLUG_PATTERN}'", name="key_format"),
        CheckConstraint(f"kind IN ({', '.join(repr(k) for k in SOURCE_KINDS)})", name="kind"),
        CheckConstraint(f"tls_mode IN ({', '.join(repr(m) for m in TLS_MODES)})", name="tls_mode"),
        CheckConstraint("port BETWEEN 1 AND 65535", name="port"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    key: Mapped[str] = mapped_column(Text)  # how people and the agent name the dataset
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    host: Mapped[str] = mapped_column(Text)
    port: Mapped[int] = mapped_column(Integer)
    database: Mapped[str] = mapped_column(Text)
    username: Mapped[str] = mapped_column(Text)
    password: Mapped[bytes | None] = mapped_column(LargeBinary)  # AES-GCM ciphertext
    tls_mode: Mapped[str] = mapped_column(String(16), server_default="verify-full")
    tls_ca: Mapped[str | None] = mapped_column(Text)  # PEM: a CA to trust for verify-full
    # The schemas (MySQL: databases) its tables may come from.
    schemas: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    yaml: Mapped[str | None] = mapped_column(Text)  # the semantic model, in the source's SQL
    enabled: Mapped[bool] = mapped_column(server_default=sa_true())
    # The last connection test or introspection: when, the server's version, or why it failed.
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_version: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None]
    updated_by: Mapped[uuid.UUID | None]


class AnalyticsRun(Base, TenantScoped):
    """A question to the BI agent and its answer (chart, narrative, SQL, data)."""

    __tablename__ = "analytics_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
        ),
        CheckConstraint(f"status IN ({', '.join(repr(s) for s in RUN_STATUSES)})", name="status"),
        Index("ix_analytics_runs_user", "tenant_id", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    user_id: Mapped[uuid.UUID]
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="running")
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
