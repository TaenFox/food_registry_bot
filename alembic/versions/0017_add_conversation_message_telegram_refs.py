"""add conversation message telegram refs

Revision ID: 0017_conversation_msg_refs
Revises: 0016_conversation_session_memory
Create Date: 2026-05-20 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0017_conversation_msg_refs"
down_revision = "0016_conversation_session_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversation_messages", sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True))
    op.add_column("conversation_messages", sa.Column("telegram_message_id", sa.BigInteger(), nullable=True))
    op.create_index(
        op.f("ix_conversation_messages_telegram_chat_id"),
        "conversation_messages",
        ["telegram_chat_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_messages_telegram_message_id"),
        "conversation_messages",
        ["telegram_message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_conversation_messages_telegram_message_id"), table_name="conversation_messages")
    op.drop_index(op.f("ix_conversation_messages_telegram_chat_id"), table_name="conversation_messages")
    op.drop_column("conversation_messages", "telegram_message_id")
    op.drop_column("conversation_messages", "telegram_chat_id")
