"""add day progress bar setting

Revision ID: 0025_day_progress_bar_setting
Revises: 0024_add_user_llm_provider_policy
Create Date: 2026-06-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_day_progress_bar_setting"
down_revision = "0024_add_user_llm_provider_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_summary_preferences",
        sa.Column("show_day_progress_bar", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("user_summary_preferences", "show_day_progress_bar", server_default=None)


def downgrade() -> None:
    op.drop_column("user_summary_preferences", "show_day_progress_bar")
