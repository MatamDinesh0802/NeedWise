"""Confluence and SharePoint/OneDrive sources; where a synced document sits in its source

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-13 12:00:00.000000

Two new source kinds (`confluence`, `sharepoint`) widen the `sources.kind` check, and
`documents.source_meta` keeps a synced item's place in its source (a Confluence page's space
and ancestors, a SharePoint file's folder path). Both tables already have forced row-level
security; adding a nullable column rewrites nothing.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "'web', 'github', 'slack', 'gdrive'"
NEW_KINDS = f"{OLD_KINDS}, 'confluence', 'sharepoint'"


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
    op.drop_constraint(op.f("ck_sources_kind"), "sources", type_="check")
    op.create_check_constraint(op.f("ck_sources_kind"), "sources", f"kind IN ({NEW_KINDS})")
    op.add_column(
        "documents",
        sa.Column("source_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "source_meta")
    # The older schema has no place for these sources. Their documents stay, as uploads
    # (documents.source_id is set to NULL with the source).
    _for_each_tenant("DELETE FROM sources WHERE kind IN ('confluence', 'sharepoint')")
    op.drop_constraint(op.f("ck_sources_kind"), "sources", type_="check")
    op.create_check_constraint(op.f("ck_sources_kind"), "sources", f"kind IN ({OLD_KINDS})")
