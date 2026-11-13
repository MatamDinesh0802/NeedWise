"""Usage events and budgets (docs/usage.md). Migration 0017.

`usage_events` is append-only for the runtime role (no UPDATE, DELETE or TRUNCATE): one row per
model call, with counts and attribution, never a prompt or an answer.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from needwise.core.db import Base, TenantScoped, Timestamps, uuid_primary_key
from needwise.modules.usage.calls import FEATURES

STATUSES = ("ok", "failed", "cancelled")
SOURCES = ("live", "backfill")
SCOPES = ("tenant", "department", "user")
PERIODS = ("day", "month")
ACTIONS = ("block", "warn")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class UsageEvent(Base, TenantScoped):
    """One model call: who it was for, what it used and cost. Counts only, no text."""

    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (user_id)",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            ondelete="SET NULL (department_id)",
        ),
        CheckConstraint(_in("feature", FEATURES), name="feature"),
        CheckConstraint(_in("status", STATUSES), name="status"),
        CheckConstraint(_in("source", SOURCES), name="source"),
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0", name="tokens"),
        Index("ix_usage_events_time", "tenant_id", "occurred_at"),
        Index("ix_usage_events_user", "tenant_id", "user_id", "occurred_at"),
        Index("ix_usage_events_department", "tenant_id", "department_id", "occurred_at"),
        Index("ix_usage_events_ref", "tenant_id", "ref_type", "ref_id"),
        # The backfill imports each stored record at most once (docs/usage.md, "Backfill").
        Index(
            "uq_usage_events_backfill",
            "tenant_id",
            "ref_type",
            "ref_id",
            unique=True,
            postgresql_where=text("source = 'backfill'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    user_id: Mapped[uuid.UUID | None]
    # The user's department when the call was made (their earliest membership, see
    # attribution.primary_department); None for people in no department and for evaluations.
    department_id: Mapped[uuid.UUID | None]
    agent_key: Mapped[str | None] = mapped_column(Text)
    feature: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str | None] = mapped_column(Text)  # catalog id; None for backfilled rows
    provider: Mapped[str] = mapped_column(Text)
    local: Mapped[bool]
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    # None when the model has no price (local models, unknown names): never a guess.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 9))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), server_default="ok")
    source: Mapped[str] = mapped_column(String(16), server_default="live")


class Budget(Base, TenantScoped, Timestamps):
    """A spending limit in USD per UTC day or month.

    scope "tenant": the whole organization (subject_id is None). "department" (Enterprise):
    the usage attributed to one department. "user": one person's cap; subject_id None is the
    default cap for everyone without a cap of their own.
    """

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint(
            "tenant_id", "scope", "subject_id", "period", postgresql_nulls_not_distinct=True
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (updated_by)",
        ),
        CheckConstraint(_in("scope", SCOPES), name="scope"),
        CheckConstraint(_in("period", PERIODS), name="period"),
        CheckConstraint(_in("action", ACTIONS), name="action"),
        CheckConstraint("limit_usd > 0", name="limit_positive"),
        CheckConstraint("warn_at > 0 AND warn_at <= 1", name="warn_at"),
        CheckConstraint("scope <> 'tenant' OR subject_id IS NULL", name="tenant_subject"),
        CheckConstraint("scope <> 'department' OR subject_id IS NOT NULL", name="department"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    scope: Mapped[str] = mapped_column(String(16))
    subject_id: Mapped[uuid.UUID | None]
    period: Mapped[str] = mapped_column(String(8))
    limit_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    warn_at: Mapped[float] = mapped_column(Float, server_default="0.8")
    action: Mapped[str] = mapped_column(String(8), server_default="block")
    updated_by: Mapped[uuid.UUID | None]


class BudgetAlert(Base, TenantScoped):
    """A warning that was sent: at most one per budget, subject and period."""

    __tablename__ = "budget_alerts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "budget_id"], ["budgets.tenant_id", "budgets.id"], ondelete="CASCADE"
        ),
    )

    budget_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    # The person for a user cap, the department for a department budget, the tenant otherwise.
    subject_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
