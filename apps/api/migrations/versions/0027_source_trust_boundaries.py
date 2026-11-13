"""Sources: who supplied the credentials, whether private addresses may be crawled, and
documents a source deleted while a legal hold kept them

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-13 12:00:00.000000

- `sources.credentials_by`: the person whose credentials are stored (pasted or connected).
  A change to what a source reads by anyone else drops them (docs/connectors.md).
- `sources.private_network`: whether a web source may reach private and loopback addresses;
  true only when an org admin last set what it reads (docs/security/threat-model.md).
- `documents.source_deleted_at`: the source reported the item deleted while a legal hold kept
  it; the first sync after no hold covers it deletes it.

Existing sources with credentials take their owner as the person who supplied them. Existing
web sources whose owner is an org owner or admin keep reaching private addresses.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _for_each_tenant(statement: str) -> None:
    """Runs a data statement once per tenant. Tenant tables have FORCED row-level security, so
    even the owner role sees only the rows of the tenant in app.tenant_id."""
    bind = op.get_bind()
    for (tenant_id,) in bind.execute(sa.text("SELECT id FROM tenants")).all():
        bind.execute(
            sa.text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        bind.execute(sa.text(statement))
    bind.execute(sa.text("SELECT set_config('app.tenant_id', '', true)"))


def upgrade() -> None:
    op.add_column("sources", sa.Column("credentials_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_sources_tenant_id_credentials_by_users"),
        "sources",
        "users",
        ["tenant_id", "credentials_by"],
        ["tenant_id", "id"],
        ondelete="SET NULL (credentials_by)",
    )
    op.add_column(
        "sources",
        sa.Column(
            "private_network", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "documents", sa.Column("source_deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    _for_each_tenant(
        "UPDATE sources SET credentials_by = owner_id WHERE credentials IS NOT NULL"
    )
    _for_each_tenant(
        "UPDATE sources SET private_network = true WHERE kind = 'web' AND owner_id IN "
        "(SELECT id FROM users WHERE org_role IN ('owner', 'admin'))"
    )


def downgrade() -> None:
    op.drop_column("documents", "source_deleted_at")
    op.drop_column("sources", "private_network")
    op.drop_constraint(
        op.f("fk_sources_tenant_id_credentials_by_users"), "sources", type_="foreignkey"
    )
    op.drop_column("sources", "credentials_by")
