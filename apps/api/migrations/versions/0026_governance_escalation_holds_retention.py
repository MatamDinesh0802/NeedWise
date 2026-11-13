"""Review escalation, legal holds and document retention, with RLS

Revision ID: 0026
Revises: 0019
Create Date: 2026-11-13 10:00:00.000000

- collections: the grace period before an overdue official review escalates, and the period
  after which it is demoted (null: never); documents: the escalation stage of this cycle.
- legal_holds (Enterprise feature "legal_hold", one schema for both editions; the core enforces
  active holds on every deletion path), with forced row-level security.
- document_retention_rules (Enterprise feature "retention_policies": per collection, delete or
  archive documents not updated for N days), with forced row-level security; documents: when
  one was archived, and when it was last restored from the archive.
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ESCALATION_STAGES = "'collection_owner', 'admins', 'demoted'"
HOLD_TARGETS = "'document', 'collection', 'user', 'meeting', 'analytics_run'"


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
    )


def _timestamps() -> list[sa.Column[datetime]]:
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
    # --- Review escalation (Community) ----------------------------------------------------------
    op.add_column(
        "collections",
        sa.Column("review_escalation_days", sa.Integer(), server_default="14", nullable=False),
    )
    op.add_column("collections", sa.Column("review_auto_demote_days", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_collections_review_escalation_days"),
        "collections",
        "review_escalation_days BETWEEN 1 AND 365",
    )
    op.create_check_constraint(
        op.f("ck_collections_review_auto_demote_days"),
        "collections",
        "review_auto_demote_days IS NULL OR review_auto_demote_days BETWEEN 1 AND 3650",
    )
    op.add_column("documents", sa.Column("review_escalation", sa.String(16), nullable=True))
    op.add_column(
        "documents", sa.Column("review_escalated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_documents_review_escalation"),
        "documents",
        f"review_escalation IS NULL OR review_escalation IN ({ESCALATION_STAGES})",
    )

    # --- Legal holds (Enterprise) ---------------------------------------------------------------
    op.create_table(
        "legal_holds",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("matter", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("placed_by", sa.Uuid(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.Uuid(), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            f"target_type IN ({HOLD_TARGETS})", name=op.f("ck_legal_holds_target_type")
        ),
        sa.CheckConstraint(
            "length(btrim(matter)) BETWEEN 1 AND 200", name=op.f("ck_legal_holds_matter")
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) BETWEEN 1 AND 2000", name=op.f("ck_legal_holds_reason")
        ),
        sa.CheckConstraint(
            "(released_at IS NULL) = (release_reason IS NULL)",
            name=op.f("ck_legal_holds_released"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "placed_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_legal_holds_tenant_id_placed_by_users"),
            ondelete="SET NULL (placed_by)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "released_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_legal_holds_tenant_id_released_by_users"),
            ondelete="SET NULL (released_by)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_legal_holds_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_holds")),
    )
    op.create_index(
        "ix_legal_holds_active_target",
        "legal_holds",
        ["tenant_id", "target_type", "target_id"],
        unique=False,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    _enable_rls("legal_holds")

    # --- Document retention (Enterprise) --------------------------------------------------------
    op.add_column("documents", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "document_retention_rules",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_run",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "days BETWEEN 30 AND 36500", name=op.f("ck_document_retention_rules_days")
        ),
        sa.CheckConstraint(
            "action IN ('delete', 'archive')", name=op.f("ck_document_retention_rules_action")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "collection_id"],
            ["collections.tenant_id", "collections.id"],
            name=op.f("fk_document_retention_rules_tenant_id_collection_id_collections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["users.tenant_id", "users.id"],
            name=op.f("fk_document_retention_rules_tenant_id_updated_by_users"),
            ondelete="SET NULL (updated_by)",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_document_retention_rules_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_retention_rules")),
        sa.UniqueConstraint(
            "tenant_id",
            "collection_id",
            name=op.f("uq_document_retention_rules_tenant_id_collection_id"),
        ),
    )
    _enable_rls("document_retention_rules")


def downgrade() -> None:
    op.drop_table("document_retention_rules")
    op.drop_column("documents", "restored_at")
    op.drop_column("documents", "archived_at")
    op.drop_table("legal_holds")

    op.drop_constraint(op.f("ck_documents_review_escalation"), "documents", type_="check")
    op.drop_column("documents", "review_escalated_at")
    op.drop_column("documents", "review_escalation")
    op.drop_constraint(op.f("ck_collections_review_auto_demote_days"), "collections", type_="check")
    op.drop_constraint(op.f("ck_collections_review_escalation_days"), "collections", type_="check")
    op.drop_column("collections", "review_auto_demote_days")
    op.drop_column("collections", "review_escalation_days")
