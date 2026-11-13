"""A meeting keeps its topic outline: meetings.topics

Revision ID: 0024
Revises: 0019
Create Date: 2026-11-13 09:00:00.000000

Each topic has a title and the transcript time range it covers ([{"title", "segment", "start",
"end", "source"}], docs/meetings.md). The column is on an existing table: its row-level security
already applies. Meetings processed before this have no outline ([]).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column(
            "topics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("meetings", "topics")
