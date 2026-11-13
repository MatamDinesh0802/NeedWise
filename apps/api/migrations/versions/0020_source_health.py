"""Source health: run history (source_runs, with RLS), failure episodes, stale synced documents

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-13 10:00:00.000000

- `source_runs`: one row per sync (trigger, status, counts, raw errors), for the run history
  in Admin > Sources. A tenant table: forced row-level security with `tenant_isolation`.
- `sources`: consecutive failures and when the owner was alerted (once per failure episode),
  and after how many days without a change in the source a synced document is stale.
- `documents`: when the item last changed in the source, since when a cut-short scan no longer
  lists it, and when its owner was reminded that it looks stale (once per episode).

Existing synced documents take the date of their current version as `source_changed_at`;
existing Slack sources get no staleness threshold (their documents are an archive), the others
180 days.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
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
    op.create_table(
        "source_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(length=16), server_default="schedule", nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="running", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'ok', 'error')", name=op.f("ck_source_runs_status")
        ),
        sa.CheckConstraint(
            "trigger IN ('schedule', 'manual')", name=op.f("ck_source_runs_trigger")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_source_runs_tenant_id_requested_by_users"),
            ondelete="SET NULL (requested_by)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["sources.tenant_id", "sources.id"],
            name=op.f("fk_source_runs_tenant_id_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_source_runs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_runs")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_source_runs_tenant_id_id")),
    )
    op.create_index(
        "ix_source_runs_source", "source_runs", ["tenant_id", "source_id", "started_at"]
    )
    op.execute("ALTER TABLE source_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE source_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON source_runs "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
    )

    op.add_column(
        "sources",
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "sources", sa.Column("failure_alerted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("sources", sa.Column("stale_after_days", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_sources_stale_after"),
        "sources",
        "stale_after_days IS NULL OR stale_after_days BETWEEN 1 AND 3650",
    )

    op.add_column(
        "documents", sa.Column("source_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("source_missing_since", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("stale_reminded_at", sa.DateTime(timezone=True), nullable=True)
    )

    # A failing source keeps its history: a source already in error has failed at least once.
    _for_each_tenant("UPDATE sources SET consecutive_failures = 1 WHERE status = 'error'")
    _for_each_tenant("UPDATE sources SET stale_after_days = 180 WHERE kind <> 'slack'")
    _for_each_tenant(
        """
        UPDATE documents d SET source_changed_at = COALESCE(
            (SELECT v.created_at FROM document_versions v WHERE v.id = d.current_version_id),
            d.created_at
        )
        WHERE d.source_id IS NOT NULL AND d.source_changed_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("documents", "stale_reminded_at")
    op.drop_column("documents", "source_missing_since")
    op.drop_column("documents", "source_changed_at")
    op.drop_constraint(op.f("ck_sources_stale_after"), "sources", type_="check")
    op.drop_column("sources", "stale_after_days")
    op.drop_column("sources", "failure_alerted_at")
    op.drop_column("sources", "consecutive_failures")
    op.drop_index("ix_source_runs_source", table_name="source_runs")
    op.drop_table("source_runs")
