"""add extracted_sections to uploaded_papers

Revision ID: 2f1c5a6b7d8e
Revises: a4c8d2e6f1b3
Create Date: 2026-03-30 20:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "2f1c5a6b7d8e"
down_revision: Union[str, Sequence[str], None] = "a4c8d2e6f1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "uploaded_papers",
        sa.Column(
            "extracted_sections",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("uploaded_papers", "extracted_sections")
