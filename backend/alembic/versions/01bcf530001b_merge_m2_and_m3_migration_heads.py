"""merge m2 and m3 migration heads

Revision ID: 01bcf530001b
Revises: b5c7e2f19a83, d4dfd7814e8f
Create Date: 2026-03-19 10:45:42.863440

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '01bcf530001b'
down_revision: Union[str, Sequence[str], None] = ('b5c7e2f19a83', 'd4dfd7814e8f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
