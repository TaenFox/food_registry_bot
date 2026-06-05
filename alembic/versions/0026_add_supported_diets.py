"""add supported diets and diet preferences

Revision ID: 0026_add_supported_diets
Revises: 0025_day_progress_bar_setting
Create Date: 2026-06-03 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_add_supported_diets"
down_revision = "0025_day_progress_bar_setting"
branch_labels = None
depends_on = None


SUPPORTED_DIETS = [
    {"code": "low_purine", "name": "Низкопуриновая", "is_enabled": True},
]

SUPPORTED_DIET_METRICS = [
    {"code": "low_purine_score", "name": "Low Purine Score", "unit": "score"},
]


def upgrade() -> None:
    op.create_table(
        "supported_diets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_supported_diets_code"),
    )
    op.create_index("ix_supported_diets_code", "supported_diets", ["code"], unique=True)

    op.create_table(
        "user_diet_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("diet_id", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["diet_id"], ["supported_diets.id"], name="fk_user_diet_preferences_diet_id_supported_diets"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_diet_preferences_user_id_users"),
        sa.UniqueConstraint("user_id", "diet_id", name="uq_user_diet_preferences_user_id_diet_id"),
    )
    op.create_index("ix_user_diet_preferences_user_id", "user_diet_preferences", ["user_id"], unique=False)
    op.create_index("ix_user_diet_preferences_diet_id", "user_diet_preferences", ["diet_id"], unique=False)

    diets_table = sa.table(
        "supported_diets",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("is_enabled", sa.Boolean()),
    )
    metrics_table = sa.table(
        "supported_metrics",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("unit", sa.String()),
    )
    op.bulk_insert(diets_table, SUPPORTED_DIETS)
    op.bulk_insert(metrics_table, SUPPORTED_DIET_METRICS)


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM entry_item_metrics
            WHERE metric_id IN (
                SELECT id FROM supported_metrics WHERE code = 'low_purine_score'
            )
            """
        )
    )
    op.execute(sa.text("DELETE FROM supported_metrics WHERE code = 'low_purine_score'"))

    op.drop_index("ix_user_diet_preferences_diet_id", table_name="user_diet_preferences")
    op.drop_index("ix_user_diet_preferences_user_id", table_name="user_diet_preferences")
    op.drop_table("user_diet_preferences")

    op.drop_index("ix_supported_diets_code", table_name="supported_diets")
    op.drop_table("supported_diets")
