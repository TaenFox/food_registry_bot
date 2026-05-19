"""add water summary and goal fields

Revision ID: 0013_water_summary_goal
Revises: 0012_add_macro_goals
Create Date: 2026-05-20 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0013_water_summary_goal"
down_revision = "0012_add_macro_goals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_summary_preferences",
        sa.Column("show_water", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "user_goal_preferences",
        sa.Column("water_goal", sa.Integer(), nullable=False, server_default="2000"),
    )
    op.add_column(
        "daily_goal_snapshots",
        sa.Column("water_goal", sa.Integer(), nullable=False, server_default="2000"),
    )


def downgrade() -> None:
    op.drop_column("daily_goal_snapshots", "water_goal")
    op.drop_column("user_goal_preferences", "water_goal")
    op.drop_column("user_summary_preferences", "show_water")
