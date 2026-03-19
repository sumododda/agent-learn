"""add user_id to courses

Revision ID: a3f8c9d12e45
Revises: 81bf14b0a537
Create Date: 2026-03-18 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f8c9d12e45'
down_revision: Union[str, Sequence[str]] = '81bf14b0a537'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable user_id column to courses table."""
    op.add_column('courses', sa.Column('user_id', sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove user_id column from courses table."""
    op.drop_column('courses', 'user_id')
