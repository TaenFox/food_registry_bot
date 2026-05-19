"""add bju summary preferences

Revision ID: 0008_bju_summary_preferences
Revises: 0007_user_summary_preferences
Create Date: 2026-05-19 22:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_bju_summary_preferences"
down_revision = "0007_user_summary_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_summary_preferences",
        sa.Column("show_protein", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "user_summary_preferences",
        sa.Column("show_fat", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "user_summary_preferences",
        sa.Column("show_carbs", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("user_summary_preferences", "show_carbs")
    op.drop_column("user_summary_preferences", "show_fat")
    op.drop_column("user_summary_preferences", "show_protein")
