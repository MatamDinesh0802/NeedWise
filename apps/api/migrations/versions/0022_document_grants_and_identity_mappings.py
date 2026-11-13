"""Document access lists that narrow their collection's, and identity mappings, with RLS

Revision ID: 0022
Revises: 0019
Create Date: 2026-11-13 10:00:00.000000

ADR 0012, docs/permission-sync.md. `documents.own_acl` marks a document with its own access
list (`document_grants`): only principals on it who can also view the collection see it.
`identity_mappings` say who a principal named by a source (a group, a domain, a Slack or GitHub
account) is in WorkMind. Existing documents keep their collection's access (own_acl false), so
nothing changes until permission sync or an editor sets a list. Both new tables carry
tenant_id with forced row-level security.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRINCIPAL = r"^(user|group|dept|dept-manager|org):[0-9a-f-]{36}$"
EXTERNAL = (
    r"^(anyone|(email|group|domain|slack-user|github-user|github-team|github-org):[^\s:]{1,255})$"
)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
    )


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("own_acl", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("documents", sa.Column("acl_origin", sa.String(length=16), nullable=True))
    op.add_column(
        "documents", sa.Column("acl_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_documents_acl_origin"),
        "documents",
        "(own_acl AND acl_origin IN ('source', 'manual')) OR (NOT own_acl AND acl_origin IS NULL)",
    )

    op.create_table(
        "document_grants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("principal", sa.Text(), nullable=True),
        sa.Column("external", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            f"principal IS NULL OR principal ~ '{PRINCIPAL}'",
            name=op.f("ck_document_grants_principal_format"),
        ),
        sa.CheckConstraint(
            "principal IS NOT NULL OR external IS NOT NULL", name=op.f("ck_document_grants_named")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name=op.f("fk_document_grants_tenant_id_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_document_grants_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_grants")),
        sa.UniqueConstraint(
            "document_id",
            "principal",
            "external",
            name=op.f("uq_document_grants_document_id_principal_external"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_document_grants_external", "document_grants", ["tenant_id", "external"], unique=False
    )
    _rls("document_grants")

    op.create_table(
        "identity_mappings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("external", sa.Text(), nullable=False),
        sa.Column("principal", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
            f"external ~ '{EXTERNAL}'", name=op.f("ck_identity_mappings_external_format")
        ),
        sa.CheckConstraint(
            f"principal ~ '{PRINCIPAL}'", name=op.f("ck_identity_mappings_principal_format")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_identity_mappings_tenant_id_created_by_users"),
            ondelete="SET NULL (created_by)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_identity_mappings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_mappings")),
        sa.UniqueConstraint(
            "tenant_id", "external", name=op.f("uq_identity_mappings_tenant_id_external")
        ),
    )
    _rls("identity_mappings")


def downgrade() -> None:
    op.drop_table("identity_mappings")
    op.drop_index("ix_document_grants_external", table_name="document_grants")
    op.drop_table("document_grants")
    op.drop_constraint(op.f("ck_documents_acl_origin"), "documents", type_="check")
    for column in ("acl_updated_at", "acl_origin", "own_acl"):
        op.drop_column("documents", column)
