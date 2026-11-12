"""Knowledge governance (owners, review cycles), superseded approvals, retention policies, with RLS

Revision ID: 0018
Revises: 0016
Create Date: 2026-11-12 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RETENTION_FIELDS = (
    "conversations_days",
    "analytics_runs_days",
    "meeting_recordings_days",
    "read_notifications_days",
)
APPROVAL_STATUSES = "'pending', 'approved', 'rejected', 'expired', 'executed', 'failed'"


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
    # --- Collections: review cycle --------------------------------------------------------------
    op.add_column("collections", sa.Column("review_interval_days", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_collections_review_interval_days"),
        "collections",
        "review_interval_days IS NULL OR review_interval_days BETWEEN 1 AND 3650",
    )

    # --- Documents: owner and review state ------------------------------------------------------
    op.add_column("documents", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("reviewed_by", sa.Uuid(), nullable=True))
    op.add_column(
        "documents", sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("documents", sa.Column("review_task_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_documents_tenant_id_owner_id_users"),
        "documents",
        "users",
        ["tenant_id", "owner_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL (owner_id)",
    )
    op.create_foreign_key(
        op.f("fk_documents_tenant_id_reviewed_by_users"),
        "documents",
        "users",
        ["tenant_id", "reviewed_by"],
        ["tenant_id", "id"],
        ondelete="SET NULL (reviewed_by)",
    )
    op.create_index(
        "ix_documents_next_review",
        "documents",
        ["tenant_id", "next_review_at"],
        unique=False,
        postgresql_where=sa.text("next_review_at IS NOT NULL"),
    )
    # Existing documents: owned by whoever uploaded them, else by the collection's owner.
    _for_each_tenant(
        """
        UPDATE documents d SET owner_id = COALESCE(
            (SELECT u.id FROM users u WHERE u.id = d.created_by AND u.tenant_id = d.tenant_id),
            (SELECT c.owner_id FROM collections c WHERE c.id = d.collection_id)
        )
        WHERE d.owner_id IS NULL
        """
    )

    # --- Meetings: a recording deleted by retention --------------------------------------------
    op.add_column(
        "meetings", sa.Column("media_deleted_at", sa.DateTime(timezone=True), nullable=True)
    )

    # --- Approvals: "superseded" when another approver of the same request decided ------------
    op.drop_constraint(op.f("ck_approvals_status"), "approvals", type_="check")
    op.create_check_constraint(
        op.f("ck_approvals_status"),
        "approvals",
        f"status IN ({APPROVAL_STATUSES}, 'superseded')",
    )

    # --- Retention policies (Enterprise feature; one schema for both editions) ----------------
    op.create_table(
        "retention_policies",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        *(sa.Column(field, sa.Integer(), nullable=True) for field in RETENTION_FIELDS),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_run",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        *(
            sa.CheckConstraint(
                f"{field} IS NULL OR {field} BETWEEN 30 AND 36500",
                name=op.f(f"ck_retention_policies_{field}"),
            )
            for field in RETENTION_FIELDS
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_retention_policies_tenant_id_updated_by_users"),
            ondelete="SET NULL (updated_by)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_retention_policies_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retention_policies")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_retention_policies_tenant_id")),
    )
    op.execute("ALTER TABLE retention_policies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE retention_policies FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON retention_policies "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
    )


def downgrade() -> None:
    op.drop_table("retention_policies")

    _for_each_tenant("UPDATE approvals SET status = 'expired' WHERE status = 'superseded'")
    op.drop_constraint(op.f("ck_approvals_status"), "approvals", type_="check")
    op.create_check_constraint(
        op.f("ck_approvals_status"), "approvals", f"status IN ({APPROVAL_STATUSES})"
    )

    op.drop_column("meetings", "media_deleted_at")

    op.drop_index("ix_documents_next_review", table_name="documents")
    op.drop_constraint(
        op.f("fk_documents_tenant_id_reviewed_by_users"), "documents", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_documents_tenant_id_owner_id_users"), "documents", type_="foreignkey"
    )
    for column in ("review_task_id", "next_review_at", "reviewed_by", "reviewed_at", "owner_id"):
        op.drop_column("documents", column)

    op.drop_constraint(op.f("ck_collections_review_interval_days"), "collections", type_="check")
    op.drop_column("collections", "review_interval_days")
