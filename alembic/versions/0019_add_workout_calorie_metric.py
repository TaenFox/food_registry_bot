"""add workout calorie metric

Revision ID: 0019_workout_calorie_metric
Revises: 0018_workout_logging_flag
Create Date: 2026-05-21 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0019_workout_calorie_metric"
down_revision = "0018_workout_logging_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO supported_metrics (code, name, unit)
            SELECT 'workout_calories', 'Workout Calories', 'kcal'
            WHERE NOT EXISTS (
                SELECT 1 FROM supported_metrics WHERE code = 'workout_calories'
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM supported_metrics WHERE code = 'workout_calories'"))
