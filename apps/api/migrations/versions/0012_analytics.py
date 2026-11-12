"""Analytics: per-tenant semantic models and datasets, and BI agent runs, with RLS

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-12 13:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("analytics_models", "analytics_runs")


def upgrade() -> None:
    op.create_table(
        "analytics_models",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("yaml", sa.Text(), nullable=True),
        sa.Column("dataset_version", sa.String(length=64), nullable=True),
        sa.Column(
            "dataset_tables",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_analytics_models_tenant_id_updated_by_users"),
            ondelete="SET NULL (updated_by)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_analytics_models_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analytics_models")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_analytics_models_tenant_id")),
    )
    op.create_table(
        "analytics_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="running", nullable=False),
        sa.Column(
            "answer",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'failed')", name=op.f("ck_analytics_runs_status")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_analytics_runs_tenant_id_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_analytics_runs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analytics_runs")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_analytics_runs_tenant_id_id")),
    )
    op.create_index(
        "ix_analytics_runs_user",
        "analytics_runs",
        ["tenant_id", "user_id", "created_at"],
        unique=False,
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
        )


def downgrade() -> None:
    op.drop_index("ix_analytics_runs_user", table_name="analytics_runs")
    op.drop_table("analytics_runs")
    op.drop_table("analytics_models")
