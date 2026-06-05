"""add gastritis supported diet

Revision ID: 0028_add_gastritis_supported_diet
Revises: 0027_add_insulin_resistance_supported_diet
Create Date: 2026-06-04 13:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_add_gastritis_supported_diet"
down_revision = "0027_add_insulin_resistance_supported_diet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO supported_diets (code, name, is_enabled)
            VALUES (:code, :name, :is_enabled)
            """
        ),
        {
            "code": "gastritis",
            "name": "При гастрите",
            "is_enabled": True,
        },
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO supported_metrics (code, name, unit)
            VALUES (:code, :name, :unit)
            """
        ),
        {
            "code": "gastritis_score",
            "name": "Gastritis Score",
            "unit": "score",
        },
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM entry_item_metrics
            WHERE metric_id IN (
                SELECT id FROM supported_metrics WHERE code = 'gastritis_score'
            )
            """
        )
    )
    op.execute(sa.text("DELETE FROM supported_metrics WHERE code = 'gastritis_score'"))
    op.execute(sa.text("DELETE FROM supported_diets WHERE code = 'gastritis'"))
