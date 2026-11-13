"""Automation: escalation, approver rules, notification delivery, request routing, YAML workflows

Revision ID: 0023
Revises: 0019
Create Date: 2026-11-13 12:00:00.000000

- approvals: the request's approver rule and stage, reminder and escalation marks, the copy an
  escalation came from, and the status "forwarded" (approved at one stage of a chain);
- tasks: how a request was routed, the triage flag, reminder and escalation marks;
- workflows: the definition as written (YAML or JSON);
- new tenant tables, each with forced row-level security: work_preferences,
  notification_deliveries, automation_settings, routing_rules;
- usage_events: the feature "routing" (request routing's model step).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TABLES = ("work_preferences", "notification_deliveries", "automation_settings", "routing_rules")
APPROVAL_STATUSES = (
    "'pending', 'approved', 'rejected', 'expired', 'executed', 'failed', 'superseded'"
)
FEATURES = (
    "'chat', 'agents', 'research', 'workflow', 'meetings', 'analytics', 'agent_test', 'eval', "
    "'other'"
)


def _for_each_tenant(statement: str) -> None:
    """Runs a data statement once per tenant: tenant tables have FORCED row-level security, so
    even the owner role sees only the rows of the tenant in app.tenant_id."""
    bind = op.get_bind()
    for (tenant_id,) in bind.execute(sa.text("SELECT id FROM tenants")).all():
        bind.execute(
            sa.text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        bind.execute(sa.text(statement))
    bind.execute(sa.text("SELECT set_config('app.tenant_id', '', true)"))


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["tenants.id"],
        name=op.f(f"fk_{table}_tenant_id_tenants"),
        ondelete="CASCADE",
    )


def upgrade() -> None:
    # --- Approvals ------------------------------------------------------------------------------
    op.add_column(
        "approvals",
        sa.Column("approver_rule", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "approvals", sa.Column("stage", sa.SmallInteger(), server_default="0", nullable=False)
    )
    op.add_column("approvals", sa.Column("reminded_at", sa.DateTime(timezone=True)))
    op.add_column("approvals", sa.Column("escalated_at", sa.DateTime(timezone=True)))
    op.add_column("approvals", sa.Column("escalated_from", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_approvals_tenant_id_escalated_from_approvals"),
        "approvals",
        "approvals",
        ["tenant_id", "escalated_from"],
        ["tenant_id", "id"],
        ondelete="SET NULL (escalated_from)",
    )
    op.create_index("ix_approvals_status", "approvals", ["tenant_id", "status", "created_at"])
    op.drop_constraint(op.f("ck_approvals_status"), "approvals", type_="check")
    op.create_check_constraint(
        op.f("ck_approvals_status"), "approvals", f"status IN ({APPROVAL_STATUSES}, 'forwarded')"
    )

    # --- Tasks ----------------------------------------------------------------------------------
    op.add_column(
        "tasks", sa.Column("routing", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "tasks", sa.Column("triage", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column("tasks", sa.Column("reminded_at", sa.DateTime(timezone=True)))
    op.add_column("tasks", sa.Column("escalated_at", sa.DateTime(timezone=True)))
    op.create_index("ix_tasks_triage", "tasks", ["tenant_id", "triage", "status"])

    # --- Workflows: the definition as written ---------------------------------------------------
    op.add_column("workflows", sa.Column("source", sa.Text(), nullable=True))

    # --- Usage: request routing's model calls ---------------------------------------------------
    op.drop_constraint(op.f("ck_usage_events_feature"), "usage_events", type_="check")
    op.create_check_constraint(
        op.f("ck_usage_events_feature"), "usage_events", f"feature IN ({FEATURES}, 'routing')"
    )

    # --- New tables -----------------------------------------------------------------------------
    op.create_table(
        "work_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "channels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("digest_hour", sa.SmallInteger(), server_default="8", nullable=False),
        sa.Column("backup_approver_id", sa.Uuid(), nullable=True),
        sa.Column("last_digest_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "digest_hour BETWEEN 0 AND 23", name=op.f("ck_work_preferences_digest_hour")
        ),
        sa.CheckConstraint(
            "backup_approver_id IS DISTINCT FROM user_id", name=op.f("ck_work_preferences_not_self")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_work_preferences_tenant_id_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "backup_approver_id"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_work_preferences_tenant_id_backup_approver_id_users"),
            ondelete="SET NULL (backup_approver_id)",
        ),
        _tenant_fk("work_preferences"),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_work_preferences")),
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("notification_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "channel IN ('email', 'slack_dm', 'slack_channel')",
            name=op.f("ck_notification_deliveries_channel"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')",
            name=op.f("ck_notification_deliveries_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_notification_deliveries_tenant_id_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "notification_id"],
            ["notifications.tenant_id", "notifications.id"],
            name=op.f("fk_notification_deliveries_tenant_id_notification_id_notifications"),
            ondelete="CASCADE",
        ),
        _tenant_fk("notification_deliveries"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_notification_deliveries_tenant_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "channel",
            "dedupe_key",
            name=op.f("uq_notification_deliveries_tenant_id_channel_dedupe_key"),
        ),
    )
    op.create_index(
        "ix_notification_deliveries_due",
        "notification_deliveries",
        ["tenant_id", "status", "next_attempt_at"],
    )

    op.create_table(
        "automation_settings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column(
            "escalation",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "approver_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "routing",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("slack_bot_token", sa.LargeBinary(), nullable=True),
        sa.Column("slack_webhook_url", sa.LargeBinary(), nullable=True),
        sa.Column(
            "slack_channel_categories",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_automation_settings_tenant_id_updated_by_users"),
            ondelete="SET NULL (updated_by)",
        ),
        _tenant_fk("automation_settings"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_settings")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_automation_settings_tenant_id")),
    )

    op.create_table(
        "routing_rules",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column(
            "keywords",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "categories",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            name=op.f("fk_routing_rules_tenant_id_department_id_departments"),
            ondelete="CASCADE",
        ),
        _tenant_fk("routing_rules"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routing_rules")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_routing_rules_tenant_id_id")),
    )
    op.create_index("ix_routing_rules_position", "routing_rules", ["tenant_id", "position"])

    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
        )


def downgrade() -> None:
    op.drop_index("ix_routing_rules_position", table_name="routing_rules")
    op.drop_table("routing_rules")
    op.drop_table("automation_settings")
    op.drop_index("ix_notification_deliveries_due", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_table("work_preferences")

    # Usage events are append-only for the runtime; the owner role may still rewrite them.
    _for_each_tenant("UPDATE usage_events SET feature = 'other' WHERE feature = 'routing'")
    op.drop_constraint(op.f("ck_usage_events_feature"), "usage_events", type_="check")
    op.create_check_constraint(
        op.f("ck_usage_events_feature"), "usage_events", f"feature IN ({FEATURES})"
    )

    op.drop_column("workflows", "source")

    op.drop_index("ix_tasks_triage", table_name="tasks")
    for column in ("escalated_at", "reminded_at", "triage", "routing"):
        op.drop_column("tasks", column)

    _for_each_tenant("UPDATE approvals SET status = 'approved' WHERE status = 'forwarded'")
    op.drop_constraint(op.f("ck_approvals_status"), "approvals", type_="check")
    op.create_check_constraint(
        op.f("ck_approvals_status"), "approvals", f"status IN ({APPROVAL_STATUSES})"
    )
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_constraint(
        op.f("fk_approvals_tenant_id_escalated_from_approvals"), "approvals", type_="foreignkey"
    )
    for column in ("escalated_from", "escalated_at", "reminded_at", "stage", "approver_rule"):
        op.drop_column("approvals", column)
