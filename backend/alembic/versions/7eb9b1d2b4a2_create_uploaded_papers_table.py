"""compatibility stub for rolled-back uploaded_papers table

Revision ID: 7eb9b1d2b4a2
Revises: c1a2b3d4e5f6
Create Date: 2026-03-30 15:12:31.522615

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "7eb9b1d2b4a2"
down_revision: Union[str, Sequence[str], None] = "c1a2b3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
