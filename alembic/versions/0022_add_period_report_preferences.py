"""add period report preferences

Revision ID: 0022_add_period_report_preferences
Revises: 0021_add_data_exchange_files
Create Date: 2026-05-25 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0022_period_report_prefs"
down_revision = "0021_data_exchange_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_summary_preferences",
        sa.Column("report_goal_tolerance_percent", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "user_summary_preferences",
        sa.Column("report_noticeable_entry_percentile", sa.Integer(), nullable=False, server_default="80"),
    )


def downgrade() -> None:
    op.drop_column("user_summary_preferences", "report_noticeable_entry_percentile")
    op.drop_column("user_summary_preferences", "report_goal_tolerance_percent")
