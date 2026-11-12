"""Workflow runs started by events: a "queued" status and one run per event (idempotency key)

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-12 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "status IN ('running', 'waiting_approval', 'succeeded', 'failed', 'cancelled')"
NEW = "status IN ('queued', 'running', 'waiting_approval', 'succeeded', 'failed', 'cancelled')"


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.create_unique_constraint(
        op.f("uq_workflow_runs_tenant_id_workflow_id_idempotency_key"),
        "workflow_runs",
        ["tenant_id", "workflow_id", "idempotency_key"],
    )
    op.drop_constraint(op.f("ck_workflow_runs_status"), "workflow_runs", type_="check")
    op.create_check_constraint(op.f("ck_workflow_runs_status"), "workflow_runs", NEW)


def downgrade() -> None:
    op.execute("UPDATE workflow_runs SET status = 'cancelled' WHERE status = 'queued'")
    op.drop_constraint(op.f("ck_workflow_runs_status"), "workflow_runs", type_="check")
    op.create_check_constraint(op.f("ck_workflow_runs_status"), "workflow_runs", OLD)
    op.drop_constraint(
        op.f("uq_workflow_runs_tenant_id_workflow_id_idempotency_key"),
        "workflow_runs",
        type_="unique",
    )
    op.drop_column("workflow_runs", "idempotency_key")
