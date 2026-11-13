"""Work that people and agents do (ARCHITECTURE section 14): tasks, approvals, notifications,
declarative workflows and their runs, and the automation settings around them (escalation,
notification delivery, request routing: docs/automation.md)."""

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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workmind.core.db import Base, TenantScoped, Timestamps, uuid_primary_key
from workmind.modules.identity.models import SLUG_PATTERN


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


TASK_STATUSES = ("open", "in_progress", "done", "cancelled")
# superseded: one of several approvers asked for the same request decided it first.
# forwarded: approved at one stage of a chain; the next stage's approvers decide now.
APPROVAL_STATUSES = (
    "pending",
    "approved",
    "rejected",
    "expired",
    "executed",
    "failed",
    "superseded",
    "forwarded",
)
DELIVERY_CHANNELS = ("email", "slack_dm", "slack_channel")
DELIVERY_STATUSES = ("pending", "sent", "failed")
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
        Index("ix_tasks_triage", "tenant_id", "triage", "status"),
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
    # Request routing (modules/work/routing.py): how it reached its queue, and why. A request
    # nobody could route with confidence waits in the triage queue for a person.
    routing: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    triage: Mapped[bool] = mapped_column(server_default=sa_false())
    # Escalation (modules/work/escalation.py): each happens once per task.
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Approval(Base, TenantScoped, Timestamps):
    """A proposed action with side effects, waiting for a person (ADR 0009). The decision runs the
    stored action, or the edited version of it, as the person who asked for it."""

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "requested_by"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(["tenant_id", "approver_id"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(["tenant_id", "decided_by"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "escalated_from"],
            ["approvals.tenant_id", "approvals.id"],
            ondelete="SET NULL (escalated_from)",
        ),
        CheckConstraint(_in("status", APPROVAL_STATUSES), name="status"),
        Index("ix_approvals_approver", "tenant_id", "approver_id", "status"),
        Index("ix_approvals_status", "tenant_id", "status", "created_at"),
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
    # Approver rules (modules/work/approvers.py): the request's stages, each a list of
    # selectors (None: one named approver). `stage` is the stage this copy belongs to.
    approver_rule: Mapped[list[list[dict[str, str]]] | None] = mapped_column(JSONB)
    stage: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    # Escalation (modules/work/escalation.py): once per approval, so once per stage.
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_from: Mapped[uuid.UUID | None]  # the copy whose approver this escalated past


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
    # The definition as the admin wrote it (YAML or JSON, comments kept); None when it was
    # saved as an object through the API.
    source: Mapped[str | None] = mapped_column(Text)
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


class WorkPreference(Base, TenantScoped):
    """A person's notification channels and backup approver (Settings > Notifications).

    `channels`: per category (modules/work/delivery.py), {"email": off|immediate|digest,
    "slack": bool}. In-app notifications always happen; these add copies elsewhere."""

    __tablename__ = "work_preferences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "backup_approver_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (backup_approver_id)",
        ),
        CheckConstraint("digest_hour BETWEEN 0 AND 23", name="digest_hour"),
        CheckConstraint("backup_approver_id IS DISTINCT FROM user_id", name="not_self"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    channels: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    digest_hour: Mapped[int] = mapped_column(SmallInteger, server_default="8")  # UTC
    backup_approver_id: Mapped[uuid.UUID | None]
    last_digest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotificationDelivery(Base, TenantScoped):
    """An outbox row: one email or Slack message to send, written in the same transaction as
    the notification and sent (and retried) by the beat task. A failure never blocks the
    action that caused the notification."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "notification_id"],
            ["notifications.tenant_id", "notifications.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("channel", DELIVERY_CHANNELS), name="channel"),
        CheckConstraint(_in("status", DELIVERY_STATUSES), name="status"),
        # A channel post about one thing (its kind and link) is made once, however many
        # people the notification went to.
        UniqueConstraint("tenant_id", "channel", "dedupe_key"),
        Index("ix_notification_deliveries_due", "tenant_id", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    user_id: Mapped[uuid.UUID | None]  # None: a channel post
    notification_id: Mapped[uuid.UUID | None]  # None: a digest or a channel post
    channel: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AutomationSettings(Base, TenantScoped, Timestamps):
    """One row per tenant (Admin > Automation): escalation policy, approver rules per tool,
    request routing, and Slack. Slack's bot token and incoming webhook URL are sealed with the
    deployment's encryption keys (model_catalog/secrets.py) and never returned."""

    __tablename__ = "automation_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL (updated_by)",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    escalation: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    approver_rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    routing: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    slack_bot_token: Mapped[bytes | None] = mapped_column(LargeBinary)
    slack_webhook_url: Mapped[bytes | None] = mapped_column(LargeBinary)
    slack_channel_categories: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]")
    )
    updated_by: Mapped[uuid.UUID | None]


class RoutingRule(Base, TenantScoped, Timestamps):
    """Request routing, rules first: keywords or categories that send a request to a
    department's queue (modules/work/routing.py)."""

    __tablename__ = "routing_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            ondelete="CASCADE",
        ),
        Index("ix_routing_rules_position", "tenant_id", "position"),
    )

    id: Mapped[uuid.UUID] = uuid_primary_key()
    department_id: Mapped[uuid.UUID]
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    categories: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    enabled: Mapped[bool] = mapped_column(server_default=sa_true())
