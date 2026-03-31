"""add discovery_events to courses

Revision ID: a4c8d2e6f1b3
Revises: 7eb9b1d2b4a2
Create Date: 2026-03-30 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a4c8d2e6f1b3"
down_revision: Union[str, Sequence[str], None] = "7eb9b1d2b4a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "courses",
        sa.Column(
            "discovery_events",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("courses", "discovery_events")
