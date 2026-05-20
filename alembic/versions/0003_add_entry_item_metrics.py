"""add entry item metrics tables

Revision ID: 0003_entry_item_metrics
Revises: 0002_extraction_trace
Create Date: 2026-05-18 23:40:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_entry_item_metrics"
down_revision = "0002_extraction_trace"
branch_labels = None
depends_on = None


SUPPORTED_METRICS = [
    {"code": "calories", "name": "Calories", "unit": "kcal"},
    {"code": "protein", "name": "Protein", "unit": "g"},
    {"code": "fat", "name": "Fat", "unit": "g"},
    {"code": "carbs", "name": "Carbs", "unit": "g"},
]


def upgrade() -> None:
    op.create_table(
        "supported_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_supported_metrics_code"),
    )
    op.create_index("ix_supported_metrics_code", "supported_metrics", ["code"], unique=True)

    op.create_table(
        "entry_item_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_item_id", sa.Integer(), nullable=False),
        sa.Column("metric_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["entry_item_id"], ["entry_items.id"], name="fk_entry_item_metrics_entry_item_id_entry_items"),
        sa.ForeignKeyConstraint(["metric_id"], ["supported_metrics.id"], name="fk_entry_item_metrics_metric_id_supported_metrics"),
        sa.UniqueConstraint("entry_item_id", "metric_id", name="uq_entry_item_metrics_entry_item_id_metric_id"),
    )
    op.create_index("ix_entry_item_metrics_entry_item_id", "entry_item_metrics", ["entry_item_id"], unique=False)
    op.create_index("ix_entry_item_metrics_metric_id", "entry_item_metrics", ["metric_id"], unique=False)

    metrics_table = sa.table(
        "supported_metrics",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("unit", sa.String()),
    )
    op.bulk_insert(metrics_table, SUPPORTED_METRICS)


def downgrade() -> None:
    op.drop_index("ix_entry_item_metrics_metric_id", table_name="entry_item_metrics")
    op.drop_index("ix_entry_item_metrics_entry_item_id", table_name="entry_item_metrics")
    op.drop_table("entry_item_metrics")

    op.drop_index("ix_supported_metrics_code", table_name="supported_metrics")
    op.drop_table("supported_metrics")
