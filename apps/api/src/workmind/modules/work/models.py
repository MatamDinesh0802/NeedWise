"""Work that people and agents do (ARCHITECTURE section 14): tasks, approvals, notifications,
declarative workflows and their runs."""

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
    text,
)
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workmind.core.db import Base, TenantScoped, Timestamps, uuid_primary_key
from workmind.modules.identity.models import SLUG_PATTERN


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


TASK_STATUSES = ("open", "in_progress", "done", "cancelled")
# superseded: one of several approvers asked for the same request decided it first.
APPROVAL_STATUSES = (
    "pending",
    "approved",
    "rejected",
    "expired",
    "executed",
    "failed",
    "superseded",
)
# "queued": created by an event, not started yet (start_queued claims it, exactly once).
RUN_STATUSES = ("queued", "running", "waiting_approval", "succeeded", "failed", "cancelled")


class Task(Base, TenantScoped, Timestamps):
    """A unit of work for a person or a department queue."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "assignee_id"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "department_id"], ["departments.tenant_id", "departments.id"]
        ),
        ForeignKeyConstraint(["tenant_id", "created_by"], ["users.tenant_id", "users.id"]),
        CheckConstraint(_in("status", TASK_STATUSES), name="status"),
        Index("ix_tasks_assignee", "tenant_id", "assignee_id", "status"),
        Index("ix_tasks_department", "tenant_id", "department_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    assignee_id: Mapped[uuid.UUID | None]
    department_id: Mapped[uuid.UUID | None]  # a department queue when nobody is assigned yet
    created_by: Mapped[uuid.UUID | None]
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class Approval(Base, TenantScoped, Timestamps):
    """A proposed action with side effects, waiting for a person (ADR 0009). The decision runs the
    stored action, or the edited version of it, as the person who asked for it."""

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "requested_by"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(["tenant_id", "approver_id"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(["tenant_id", "decided_by"], ["users.tenant_id", "users.id"]),
        CheckConstraint(_in("status", APPROVAL_STATUSES), name="status"),
        Index("ix_approvals_approver", "tenant_id", "approver_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    tool: Mapped[str] = mapped_column(Text)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB)  # as proposed
    edited_arguments: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # as approved
    summary: Mapped[str] = mapped_column(Text)  # what will happen, in plain words
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    requested_by: Mapped[uuid.UUID]  # the action runs as this person
    approver_id: Mapped[uuid.UUID]
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    origin: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class Notification(Base, TenantScoped, Timestamps):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
        ),
        Index("ix_notifications_user", "tenant_id", "user_id", "read_at"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    user_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, server_default="")
    link: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Workflow(Base, TenantScoped, Timestamps):
    """A declarative automation: steps (YAML) and a trigger (manual, cron schedule, or event)."""

    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "key"),
        ForeignKeyConstraint(["tenant_id", "owner_id"], ["users.tenant_id", "users.id"]),
        CheckConstraint(f"key ~ '{SLUG_PATTERN}'", name="key_format"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[uuid.UUID]  # runs act as this person
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(server_default=sa_true())
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowRun(Base, TenantScoped, Timestamps):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "workflow_id"],
            ["workflows.tenant_id", "workflows.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("status", RUN_STATUSES), name="status"),
        Index("ix_workflow_runs_workflow", "tenant_id", "workflow_id", "created_at"),
        # One run per event and workflow: a re-delivered or repeated event finds it taken.
        # NULLs are distinct, so manual and scheduled runs (no key) never collide.
        UniqueConstraint("tenant_id", "workflow_id", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    workflow_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(String(24), server_default="running")
    trigger: Mapped[str] = mapped_column(String(16))  # manual | schedule | event
    idempotency_key: Mapped[str | None] = mapped_column(Text)  # "document.ready:<version id>"
    # Per step id: {"status", "output", "error", "approval_id"}. A paused run resumes here.
    steps: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    current_step: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
