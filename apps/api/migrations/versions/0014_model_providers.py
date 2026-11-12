"""Model providers (bring your own key), the model catalog and the role mapping and data policy,
with RLS

Revision ID: 0014
Revises: 0012
Create Date: 2026-11-12 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("model_providers", "model_catalog", "model_settings")


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


def upgrade() -> None:
    op.create_table(
        "model_providers",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("api_key", sa.LargeBinary(), nullable=True),
        sa.Column("key_hint", sa.String(length=16), nullable=True),
        sa.Column("local", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('anthropic', 'openai_compatible')", name=op.f("ck_model_providers_kind")
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100", name=op.f("ck_model_providers_name_length")
        ),
        sa.CheckConstraint(
            "kind = 'anthropic' OR base_url IS NOT NULL",
            name=op.f("ck_model_providers_base_url_required"),
        ),
        sa.CheckConstraint(
            "kind = 'openai_compatible' OR NOT local",
            name=op.f("ck_model_providers_anthropic_not_local"),
        ),
        sa.CheckConstraint(
            "(api_key IS NULL) = (key_hint IS NULL)", name=op.f("ck_model_providers_key_hint")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_model_providers_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_providers")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_model_providers_tenant_id_id")),
        sa.UniqueConstraint("tenant_id", "name", name=op.f("uq_model_providers_tenant_id_name")),
    )
    op.create_table(
        "model_catalog",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("input_usd_per_mtok", sa.Double(), nullable=True),
        sa.Column("output_usd_per_mtok", sa.Double(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 200", name=op.f("ck_model_catalog_name_length")
        ),
        sa.CheckConstraint(
            "char_length(label) BETWEEN 1 AND 200", name=op.f("ck_model_catalog_label_length")
        ),
        sa.CheckConstraint(
            "(input_usd_per_mtok IS NULL OR input_usd_per_mtok >= 0) AND "
            "(output_usd_per_mtok IS NULL OR output_usd_per_mtok >= 0)",
            name=op.f("ck_model_catalog_prices"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provider_id"],
            ["model_providers.tenant_id", "model_providers.id"],
            name=op.f("fk_model_catalog_tenant_id_provider_id_model_providers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_model_catalog_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_catalog")),
        sa.UniqueConstraint(
            "tenant_id",
            "provider_id",
            "name",
            name=op.f("uq_model_catalog_tenant_id_provider_id_name"),
        ),
    )
    op.create_table(
        "model_settings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column(
            "roles",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("version", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_model_settings_tenant_id_updated_by_users"),
            ondelete="SET NULL (updated_by)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_model_settings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_settings")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_model_settings_tenant_id")),
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
        )


def downgrade() -> None:
    op.drop_table("model_settings")
    op.drop_table("model_catalog")
    op.drop_table("model_providers")
