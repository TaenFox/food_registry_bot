"""add nutrition day start hour

Revision ID: 0009_nutrition_day_start_hour
Revises: 0008_bju_summary_preferences
Create Date: 2026-05-19 23:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_nutrition_day_start_hour"
down_revision = "0008_bju_summary_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_summary_preferences",
        sa.Column("nutrition_day_start_hour", sa.Integer(), nullable=False, server_default="4"),
    )


def downgrade() -> None:
    op.drop_column("user_summary_preferences", "nutrition_day_start_hour")
