"""Agent configuration: tools, model, limits and numbered versions, with RLS

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-12 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Version 1 for definitions that existed before versioning, with the same fields as
# workmind.modules.agents.definitions.snapshot().
BACKFILL_SQL = """
    INSERT INTO agent_definition_versions (tenant_id, agent_id, version, snapshot, note)
    SELECT tenant_id, id, 1,
        jsonb_build_object(
            'key', key, 'name', name, 'description', description, 'instructions', instructions,
            'collections', to_jsonb(collection_keys), 'examples', to_jsonb(examples),
            'tools', to_jsonb(tools), 'model', model, 'max_searches', max_searches,
            'max_tool_calls', max_tool_calls, 'max_output_tokens', max_output_tokens,
            'enabled', enabled
        ),
        'Definition before versioning'
    FROM agent_definitions
"""


def upgrade() -> None:
    op.add_column(
        "agent_definitions",
        sa.Column(
            "tools",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
    )
    op.add_column("agent_definitions", sa.Column("model", sa.Text(), nullable=True))
    op.add_column(
        "agent_definitions",
        sa.Column("max_tool_calls", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "agent_definitions",
        sa.Column("max_output_tokens", sa.Integer(), server_default="6000", nullable=False),
    )
    op.add_column(
        "agent_definitions",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_agent_definitions_max_tool_calls"),
        "agent_definitions",
        "max_tool_calls BETWEEN 0 AND 5",
    )
    op.create_check_constraint(
        op.f("ck_agent_definitions_max_output_tokens"),
        "agent_definitions",
        "max_output_tokens BETWEEN 1000 AND 16000",
    )
    op.create_check_constraint(
        op.f("ck_agent_definitions_version"), "agent_definitions", "version >= 1"
    )

    op.create_table(
        "agent_definition_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_agent_definition_versions_version")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["agent_definitions.tenant_id", "agent_definitions.id"],
            name=op.f("fk_agent_definition_versions_tenant_id_agent_id_agent_definitions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "author_id"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_agent_definition_versions_tenant_id_author_id_users"),
            ondelete="SET NULL (author_id)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_agent_definition_versions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_definition_versions")),
        sa.UniqueConstraint(
            "agent_id", "version", name=op.f("uq_agent_definition_versions_agent_id_version")
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_agent_definition_versions_tenant_id_id")
        ),
    )
    op.execute("ALTER TABLE agent_definition_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_definition_versions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON agent_definition_versions "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
    )

    # Version 1 for every existing definition. RLS is forced for the owner role too, so this
    # runs once per tenant with that tenant selected (as the application does).
    connection = op.get_bind()
    tenants = connection.execute(sa.text("SELECT id FROM tenants")).scalars().all()
    for tenant_id in tenants:
        connection.execute(
            sa.text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        connection.execute(sa.text(BACKFILL_SQL))
    connection.execute(sa.text("SELECT set_config('app.tenant_id', '', true)"))


def downgrade() -> None:
    op.drop_table("agent_definition_versions")
    op.drop_constraint(op.f("ck_agent_definitions_version"), "agent_definitions", type_="check")
    op.drop_constraint(
        op.f("ck_agent_definitions_max_output_tokens"), "agent_definitions", type_="check"
    )
    op.drop_constraint(
        op.f("ck_agent_definitions_max_tool_calls"), "agent_definitions", type_="check"
    )
    for column in ("version", "max_output_tokens", "max_tool_calls", "model", "tools"):
        op.drop_column("agent_definitions", column)
