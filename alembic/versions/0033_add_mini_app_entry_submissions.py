"""Добавить защиту записи из Mini App от повторной отправки."""

import sqlalchemy as sa
from alembic import op

revision = "0033_mini_app_entry_submissions"
down_revision = "0032_entry_item_source_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mini_app_entry_submissions",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("request_id", sa.String(32), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mini_app_entry_submissions")
