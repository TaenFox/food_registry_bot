"""add user access table

Revision ID: 0005_user_access
Revises: 0004_metric_confidence
Create Date: 2026-05-19 10:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_user_access"
down_revision = "0004_metric_confidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_access",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("is_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("telegram_user_id", name="uq_user_access_telegram_user_id"),
    )
    op.create_index("ix_user_access_telegram_user_id", "user_access", ["telegram_user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_user_access_telegram_user_id", table_name="user_access")
    op.drop_table("user_access")
