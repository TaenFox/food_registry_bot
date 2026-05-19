"""add user summary preferences

Revision ID: 0007_user_summary_preferences
Revises: 0006_bigint_telegram_user_ids
Create Date: 2026-05-19 21:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_user_summary_preferences"
down_revision = "0006_bigint_telegram_user_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_summary_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("show_calories", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", name="uq_user_summary_preferences_user_id"),
    )
    op.create_index(
        "ix_user_summary_preferences_user_id",
        "user_summary_preferences",
        ["user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_user_summary_preferences_user_id", table_name="user_summary_preferences")
    op.drop_table("user_summary_preferences")
