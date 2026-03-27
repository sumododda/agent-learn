"""add_user_id_foreign_keys

Revision ID: 8fb5f073bb71
Revises: b9956d0cdc4a
Create Date: 2026-03-27 00:39:53.750973

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8fb5f073bb71'
down_revision: Union[str, Sequence[str], None] = 'b9956d0cdc4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove any courses with null user_id (pre-auth legacy data)
    op.execute("DELETE FROM courses WHERE user_id IS NULL")

    # courses.user_id: Text -> Uuid, non-nullable, add FK
    op.alter_column('courses', 'user_id',
                     type_=sa.Uuid(),
                     postgresql_using='user_id::uuid',
                     nullable=False)
    op.create_foreign_key('fk_courses_user_id', 'courses', 'users', ['user_id'], ['id'])

    # learner_progress.user_id: Text -> Uuid, add FK
    op.alter_column('learner_progress', 'user_id',
                     type_=sa.Uuid(),
                     postgresql_using='user_id::uuid',
                     nullable=False)
    op.create_foreign_key('fk_learner_progress_user_id', 'learner_progress', 'users', ['user_id'], ['id'])

    # chat_messages.user_id: Text -> Uuid, add FK
    op.alter_column('chat_messages', 'user_id',
                     type_=sa.Uuid(),
                     postgresql_using='user_id::uuid',
                     nullable=False)
    op.create_foreign_key('fk_chat_messages_user_id', 'chat_messages', 'users', ['user_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_chat_messages_user_id', 'chat_messages', type_='foreignkey')
    op.alter_column('chat_messages', 'user_id', type_=sa.Text(), postgresql_using='user_id::text')

    op.drop_constraint('fk_learner_progress_user_id', 'learner_progress', type_='foreignkey')
    op.alter_column('learner_progress', 'user_id', type_=sa.Text(), postgresql_using='user_id::text')

    op.drop_constraint('fk_courses_user_id', 'courses', type_='foreignkey')
    op.alter_column('courses', 'user_id', type_=sa.Text(), nullable=True, postgresql_using='user_id::text')
