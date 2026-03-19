"""create learner_progress table

Revision ID: b5c7e2f19a83
Revises: a3f8c9d12e45
Create Date: 2026-03-18 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5c7e2f19a83'
down_revision: Union[str, Sequence[str]] = 'a3f8c9d12e45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create learner_progress table for tracking per-user course progress."""
    op.create_table(
        'learner_progress',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('course_id', sa.Uuid(), nullable=False),
        sa.Column('current_section', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('completed_sections', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('last_accessed_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['course_id'], ['courses.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'course_id', name='uq_user_course_progress'),
    )


def downgrade() -> None:
    """Drop learner_progress table."""
    op.drop_table('learner_progress')
