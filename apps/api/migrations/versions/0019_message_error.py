"""A failed answer keeps why it failed: messages.error

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-12 22:00:00.000000

The `run.failed` event said why (for a used-up budget: which budget and when it resets), but
the stored message did not, so a reload, or the refetch when the run ends, showed only "This
answer failed." The column is on an existing table: its row-level security already applies.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "error")
