"""add workout calorie credit metric

Revision ID: 0020_workout_calorie_credit
Revises: 0019_workout_calorie_metric
Create Date: 2026-05-21 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0020_workout_calorie_credit"
down_revision = "0019_workout_calorie_metric"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO supported_metrics (code, name, unit)
            SELECT 'workout_calorie_credit', 'Workout Calorie Credit', 'kcal'
            WHERE NOT EXISTS (
                SELECT 1 FROM supported_metrics WHERE code = 'workout_calorie_credit'
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM supported_metrics WHERE code = 'workout_calorie_credit'"))
