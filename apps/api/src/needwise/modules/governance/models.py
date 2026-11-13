"""Governance: the append-only, hash-chained audit log (ARCHITECTURE §9, §20), the per-tenant
retention policy and legal holds (docs/governance.md, docs/enterprise.md)."""

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import UniqueConstraint

from needwise.core.db import Base, TenantScoped, Timestamps, uuid_primary_key


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


RETENTION_ACTIONS: Final = ("delete", "archive")


class DocumentRetentionRule(Base, TenantScoped, Timestamps):
    """Per collection (Enterprise feature "retention_policies"): documents not updated for
    `days` are deleted or archived. Updated means new content or a review (or a restore from
    the archive). Legal holds and official status are respected (needwise_ee.retention)."""

    __tablename__ = "document_retention_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "collection_id"),
        ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["collections.tenant_id", "collections.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (updated_by)",
        ),
        CheckConstraint(f"days BETWEEN {MIN_RETENTION_DAYS} AND {MAX_RETENTION_DAYS}", name="days"),
        CheckConstraint(
            f"action IN ({', '.join(repr(a) for a in RETENTION_ACTIONS)})", name="action"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    collection_id: Mapped[uuid.UUID]
    days: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16))
    updated_by: Mapped[uuid.UUID | None]
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What the last run did with this collection's documents, and what it kept and why.
    last_run: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


# Legal hold (Enterprise feature "legal_hold"; docs/enterprise.md#legal-hold): what a hold can
# name. A collection covers its documents and meetings; a person covers their conversations, the
# meetings they uploaded and their Insights questions; a meeting also covers its transcript.
HOLD_TARGETS: Final = ("document", "collection", "user", "meeting", "analytics_run")


class LegalHold(Base, TenantScoped, Timestamps):
    """Keeps what it names from being deleted, by anyone or anything, until it is released.

    Placing and releasing holds is Enterprise; the core enforces every active hold on every
    deletion path (users, connectors, retention, tenant deletion) whatever the license says, so
    a lapsed license never quietly releases one. A released hold stays as the record."""

    __tablename__ = "legal_holds"
    __table_args__ = (
        CheckConstraint(
            f"target_type IN ({', '.join(repr(t) for t in HOLD_TARGETS)})", name="target_type"
        ),
        CheckConstraint("length(btrim(matter)) BETWEEN 1 AND 200", name="matter"),
        CheckConstraint("length(btrim(reason)) BETWEEN 1 AND 2000", name="reason"),
        CheckConstraint("(released_at IS NULL) = (release_reason IS NULL)", name="released"),
        ForeignKeyConstraint(
            ["tenant_id", "placed_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (placed_by)",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "released_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (released_by)",
        ),
        # Every deletion asks "is this held?": active holds by what they name.
        Index(
            "ix_legal_holds_active_target",
            "tenant_id",
            "target_type",
            "target_id",
            postgresql_where=text("released_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    matter: Mapped[str] = mapped_column(Text)  # the case or investigation, e.g. "Smith v. Acme"
    reason: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(String(16))
    # No foreign key: one column names several kinds of record. What it names cannot be deleted
    # while the hold is active, and the tenant's deletion removes the hold with everything else.
    target_id: Mapped[uuid.UUID]
    placed_by: Mapped[uuid.UUID | None]
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[uuid.UUID | None]
    release_reason: Mapped[str | None] = mapped_column(Text)
