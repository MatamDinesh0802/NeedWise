"""Review escalation, legal holds and document retention, with RLS

Revision ID: 0026
Revises: 0019
Create Date: 2026-11-13 10:00:00.000000

- collections: the grace period before an overdue official review escalates, and the period
  after which it is demoted (null: never); documents: the escalation stage of this cycle.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ESCALATION_STAGES = "'collection_owner', 'admins', 'demoted'"


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


def downgrade() -> None:
    op.drop_constraint(op.f("ck_documents_review_escalation"), "documents", type_="check")
    op.drop_column("documents", "review_escalated_at")
    op.drop_column("documents", "review_escalation")
    op.drop_constraint(op.f("ck_collections_review_auto_demote_days"), "collections", type_="check")
    op.drop_constraint(op.f("ck_collections_review_escalation_days"), "collections", type_="check")
    op.drop_column("collections", "review_auto_demote_days")
    op.drop_column("collections", "review_escalation_days")
