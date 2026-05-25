"""add workout logging flag

Revision ID: 0018_workout_logging_flag
Revises: 0017_conversation_msg_refs
Create Date: 2026-05-21 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0018_workout_logging_flag"
down_revision = "0017_conversation_msg_refs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("workout_logging_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("users", "workout_logging_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "workout_logging_enabled")
