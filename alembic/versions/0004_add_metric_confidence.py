"""add confidence to entry item metrics

Revision ID: 0004_metric_confidence
Revises: 0003_entry_item_metrics
Create Date: 2026-05-19 00:40:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_metric_confidence"
down_revision = "0003_entry_item_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entry_item_metrics",
        sa.Column(
            "confidence",
            sa.String(length=32),
            nullable=False,
            server_default="medium",
        ),
    )


def downgrade() -> None:
    op.drop_column("entry_item_metrics", "confidence")
