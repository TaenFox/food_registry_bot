"""add fiber metric support

Revision ID: 0015_fiber_metric
Revises: 0014_post_entry_delta_suffix
Create Date: 2026-05-20 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0015_fiber_metric"
down_revision = "0014_post_entry_delta_suffix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_summary_preferences",
        sa.Column("show_fiber", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "user_goal_preferences",
        sa.Column("fiber_goal", sa.Integer(), nullable=False, server_default="25"),
    )
    op.add_column(
        "daily_goal_snapshots",
        sa.Column("fiber_goal", sa.Integer(), nullable=False, server_default="25"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO supported_metrics (code, name, unit)
            SELECT 'fiber', 'Fiber', 'g'
            WHERE NOT EXISTS (
                SELECT 1 FROM supported_metrics WHERE code = 'fiber'
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM supported_metrics WHERE code = 'fiber'"))
    op.drop_column("daily_goal_snapshots", "fiber_goal")
    op.drop_column("user_goal_preferences", "fiber_goal")
    op.drop_column("user_summary_preferences", "show_fiber")
