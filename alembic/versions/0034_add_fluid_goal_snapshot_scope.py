"""Сохранить смысл прежних целей по обычной воде."""

import sqlalchemy as sa
from alembic import op

revision = "0034_fluid_goal_snapshot_scope"
down_revision = "0033_mini_app_entry_submissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "daily_goal_snapshots",
        sa.Column("water_goal_includes_beverages", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("daily_goal_snapshots", "water_goal_includes_beverages")
