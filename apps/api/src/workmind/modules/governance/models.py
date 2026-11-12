"""Governance: the append-only, hash-chained audit log (ARCHITECTURE §9, §20) and the per-tenant
retention policy (docs/governance.md, docs/enterprise.md)."""

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import UniqueConstraint

from workmind.core.db import Base, TenantScoped, Timestamps, uuid_primary_key


class AuditEvent(Base, TenantScoped):
    __tablename__ = "audit_events"
    __table_args__ = (
        # seq orders the chain per tenant; the unique constraint also serves tenant queries.
        UniqueConstraint("tenant_id", "seq"),
        CheckConstraint("actor_type IN ('user', 'service', 'system')", name="actor_type"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    seq: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    prev_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    hash: Mapped[bytes] = mapped_column(LargeBinary)


# Retention (Enterprise feature "retention_policies"): how long each kind of data is kept.
# The table is part of the core schema so both editions share one schema (ADR 0011); only the
# Enterprise package reads it and deletes anything. Audit events are never subject to it.
MIN_RETENTION_DAYS: Final = 30
MAX_RETENTION_DAYS: Final = 36_500
RETENTION_FIELDS: Final = (
    "conversations_days",  # by last activity
    "analytics_runs_days",
    "meeting_recordings_days",  # the media file only; transcripts and notes stay
    "read_notifications_days",  # by when they were read
)


class RetentionPolicy(Base, TenantScoped, Timestamps):
    """One per tenant. A null field keeps that kind of data forever."""

    __tablename__ = "retention_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (updated_by)",
        ),
        *(
            CheckConstraint(
                f"{field} IS NULL OR {field} BETWEEN {MIN_RETENTION_DAYS} AND {MAX_RETENTION_DAYS}",
                name=field,
            )
            for field in RETENTION_FIELDS
        ),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    conversations_days: Mapped[int | None] = mapped_column(Integer)
    analytics_runs_days: Mapped[int | None] = mapped_column(Integer)
    meeting_recordings_days: Mapped[int | None] = mapped_column(Integer)
    read_notifications_days: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[uuid.UUID | None]
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What the last run deleted ({"conversations": 3, ...}) and who or what started it.
    last_run: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
