"""add post-entry delta suffix setting

Revision ID: 0014_post_entry_delta_suffix
Revises: 0013_water_summary_goal
Create Date: 2026-05-20 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0014_post_entry_delta_suffix"
down_revision = "0013_water_summary_goal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_summary_preferences",
        sa.Column("show_post_entry_delta_suffix", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("user_summary_preferences", "show_post_entry_delta_suffix")
