"""use bigint for telegram user ids

Revision ID: 0006_bigint_telegram_user_ids
Revises: 0005_user_access
Create Date: 2026-05-19 13:35:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_bigint_telegram_user_ids"
down_revision = "0005_user_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "telegram_user_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    op.alter_column(
        "user_access",
        "telegram_user_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "user_access",
        "telegram_user_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "users",
        "telegram_user_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
