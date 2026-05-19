"""add macro goals

Revision ID: 0012_add_macro_goals
Revises: 0011_summary_display_mode
Create Date: 2026-05-19 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0012_add_macro_goals"
down_revision = "0011_summary_display_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_goal_preferences",
        sa.Column("protein_goal", sa.Integer(), nullable=False, server_default="90"),
    )
    op.add_column(
        "user_goal_preferences",
        sa.Column("fat_goal", sa.Integer(), nullable=False, server_default="60"),
    )
    op.add_column(
        "user_goal_preferences",
        sa.Column("carbs_goal", sa.Integer(), nullable=False, server_default="210"),
    )

    op.add_column(
        "daily_goal_snapshots",
        sa.Column("protein_goal", sa.Integer(), nullable=False, server_default="90"),
    )
    op.add_column(
        "daily_goal_snapshots",
        sa.Column("fat_goal", sa.Integer(), nullable=False, server_default="60"),
    )
    op.add_column(
        "daily_goal_snapshots",
        sa.Column("carbs_goal", sa.Integer(), nullable=False, server_default="210"),
    )


def downgrade() -> None:
    op.drop_column("daily_goal_snapshots", "carbs_goal")
    op.drop_column("daily_goal_snapshots", "fat_goal")
    op.drop_column("daily_goal_snapshots", "protein_goal")
    op.drop_column("user_goal_preferences", "carbs_goal")
    op.drop_column("user_goal_preferences", "fat_goal")
    op.drop_column("user_goal_preferences", "protein_goal")
