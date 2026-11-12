"""Business intelligence (ARCHITECTURE section 16): each tenant's semantic model and dataset,
and the questions people asked the BI agent."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workmind.core.db import Base, TenantScoped, Timestamps, uuid_primary_key

RUN_STATUSES = ("running", "done", "failed")


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
