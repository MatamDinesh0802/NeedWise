"""Usage and budgets: usage_events (append-only), budgets, budget_alerts, with RLS

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-12 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from needwise.core.config import get_settings

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("usage_events", "budgets", "budget_alerts")
FEATURES = (
    "chat",
    "agents",
    "research",
    "workflow",
    "meetings",
    "analytics",
    "agent_test",
    "eval",
    "other",
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("agent_key", sa.Text(), nullable=True),
        sa.Column("feature", sa.String(length=16), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("local", sa.Boolean(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=18, scale=9), nullable=True),
        sa.Column("ref_type", sa.String(length=32), nullable=True),
        sa.Column("ref_id", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="ok", nullable=False),
        sa.Column("source", sa.String(length=16), server_default="live", nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(_in("feature", FEATURES), name=op.f("ck_usage_events_feature")),
        sa.CheckConstraint(
            "status IN ('ok', 'failed', 'cancelled')", name=op.f("ck_usage_events_status")
        ),
        sa.CheckConstraint("source IN ('live', 'backfill')", name=op.f("ck_usage_events_source")),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0", name=op.f("ck_usage_events_tokens")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["departments.tenant_id", "departments.id"],
            name=op.f("fk_usage_events_tenant_id_department_id_departments"),
            ondelete="SET NULL (department_id)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_usage_events_tenant_id_user_id_users"),
            ondelete="SET NULL (user_id)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_usage_events_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_events")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_usage_events_tenant_id_id")),
    )
    op.create_index("ix_usage_events_time", "usage_events", ["tenant_id", "occurred_at"])
    op.create_index("ix_usage_events_user", "usage_events", ["tenant_id", "user_id", "occurred_at"])
    op.create_index(
        "ix_usage_events_department",
        "usage_events",
        ["tenant_id", "department_id", "occurred_at"],
    )
    op.create_index("ix_usage_events_ref", "usage_events", ["tenant_id", "ref_type", "ref_id"])
    op.create_index(
        "uq_usage_events_backfill",
        "usage_events",
        ["tenant_id", "ref_type", "ref_id"],
        unique=True,
        postgresql_where=sa.text("source = 'backfill'"),
    )

    op.create_table(
        "budgets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("limit_usd", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("warn_at", sa.Float(), server_default="0.8", nullable=False),
        sa.Column("action", sa.String(length=8), server_default="block", nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint(
            "scope IN ('tenant', 'department', 'user')", name=op.f("ck_budgets_scope")
        ),
        sa.CheckConstraint("period IN ('day', 'month')", name=op.f("ck_budgets_period")),
        sa.CheckConstraint("action IN ('block', 'warn')", name=op.f("ck_budgets_action")),
        sa.CheckConstraint("limit_usd > 0", name=op.f("ck_budgets_limit_positive")),
        sa.CheckConstraint("warn_at > 0 AND warn_at <= 1", name=op.f("ck_budgets_warn_at")),
        sa.CheckConstraint(
            "scope <> 'tenant' OR subject_id IS NULL", name=op.f("ck_budgets_tenant_subject")
        ),
        sa.CheckConstraint(
            "scope <> 'department' OR subject_id IS NOT NULL", name=op.f("ck_budgets_department")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_budgets_tenant_id_updated_by_users"),
            ondelete="SET NULL (updated_by)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_budgets_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budgets")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_budgets_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id",
            "scope",
            "subject_id",
            "period",
            name=op.f("uq_budgets_tenant_id_scope_subject_id_period"),
            postgresql_nulls_not_distinct=True,
        ),
    )

    op.create_table(
        "budget_alerts",
        sa.Column("budget_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "budget_id"],
            ["budgets.tenant_id", "budgets.id"],
            name=op.f("fk_budget_alerts_tenant_id_budget_id_budgets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_budget_alerts_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "budget_id", "subject_id", "period_start", name=op.f("pk_budget_alerts")
        ),
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
        )
    # Usage history is append-only for the runtime, like the audit log.
    app_role = get_settings().db_app_role
    op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON usage_events FROM "{app_role}"')


def downgrade() -> None:
    op.drop_table("budget_alerts")
    op.drop_table("budgets")
    op.drop_index("uq_usage_events_backfill", table_name="usage_events")
    op.drop_index("ix_usage_events_ref", table_name="usage_events")
    op.drop_index("ix_usage_events_department", table_name="usage_events")
    op.drop_index("ix_usage_events_user", table_name="usage_events")
    op.drop_index("ix_usage_events_time", table_name="usage_events")
    op.drop_table("usage_events")
