"""create core tables

Revision ID: 0001_create_core_tables
Revises:
Create Date: 2026-05-18 16:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0001_create_core_tables"
down_revision = None
branch_labels = None
depends_on = None


entry_type = sa.Enum("food", "water", "workout", name="entry_type")
meal_type = sa.Enum("breakfast", "lunch", "dinner", "snack", "drink", name="meal_type")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/Moscow"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"], unique=True)

    op.create_table(
        "entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry_type", entry_type, nullable=False),
        sa.Column("meal_type", meal_type, nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("llm_comment", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_entries_user_id_users"),
    )
    op.create_index("ix_entries_user_id", "entries", ["user_id"], unique=False)
    op.create_index("ix_entries_occurred_at", "entries", ["occurred_at"], unique=False)

    op.create_table(
        "entry_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.String(length=32), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["entry_id"], ["entries.id"], name="fk_entry_items_entry_id_entries"),
        sa.UniqueConstraint("entry_id", "position", name="uq_entry_items_entry_id_position"),
    )
    op.create_index("ix_entry_items_entry_id", "entry_items", ["entry_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_entry_items_entry_id", table_name="entry_items")
    op.drop_table("entry_items")

    op.drop_index("ix_entries_occurred_at", table_name="entries")
    op.drop_index("ix_entries_user_id", table_name="entries")
    op.drop_table("entries")

    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_table("users")

    meal_type.drop(op.get_bind(), checkfirst=True)
    entry_type.drop(op.get_bind(), checkfirst=True)
