"""add summary display mode

Revision ID: 0011_summary_display_mode
Revises: 0010_user_goal_snapshots
Create Date: 2026-05-19 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0011_summary_display_mode"
down_revision = "0010_user_goal_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_summary_preferences",
        sa.Column("summary_display_mode", sa.String(length=16), nullable=False, server_default="text"),
    )


def downgrade() -> None:
    op.drop_column("user_summary_preferences", "summary_display_mode")
