"""compatibility stub for rolled-back discovery_events column

Revision ID: a4c8d2e6f1b3
Revises: 7eb9b1d2b4a2
Create Date: 2026-03-30 18:00:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "a4c8d2e6f1b3"
down_revision: Union[str, Sequence[str], None] = "7eb9b1d2b4a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
