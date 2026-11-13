"""Analytics SQL sources: PostgreSQL and MySQL databases with their own semantic model, with RLS

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-13 10:00:00.000000

One row per database an org admin connected for the BI agent (docs/analytics-sql-sources.md):
where it is, the user, the password (AES-256-GCM ciphertext, never returned), the TLS mode,
the schemas its tables may come from, and its semantic model. Tenant-owned: forced row-level
security with the tenant_isolation policy, like every tenant table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analytics_sources",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password", sa.LargeBinary(), nullable=True),
        sa.Column("tls_mode", sa.String(length=16), server_default="verify-full", nullable=False),
        sa.Column("tls_ca", sa.Text(), nullable=True),
        sa.Column(
            "schemas",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("yaml", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("server_version", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
            "key ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name=op.f("ck_analytics_sources_key_format")
        ),
        sa.CheckConstraint("kind IN ('postgres', 'mysql')", name=op.f("ck_analytics_sources_kind")),
        sa.CheckConstraint(
            "tls_mode IN ('disable', 'require', 'verify-full')",
            name=op.f("ck_analytics_sources_tls_mode"),
        ),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name=op.f("ck_analytics_sources_port")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_analytics_sources_tenant_id_created_by_users"),
            ondelete="SET NULL (created_by)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_analytics_sources_tenant_id_updated_by_users"),
            ondelete="SET NULL (updated_by)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_analytics_sources_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analytics_sources")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_analytics_sources_tenant_id_id")),
        sa.UniqueConstraint("tenant_id", "key", name=op.f("uq_analytics_sources_tenant_id_key")),
    )
    op.execute("ALTER TABLE analytics_sources ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE analytics_sources FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON analytics_sources "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
    )


def downgrade() -> None:
    op.drop_table("analytics_sources")
