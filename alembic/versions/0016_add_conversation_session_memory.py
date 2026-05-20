"""add conversation session memory

Revision ID: 0016_conversation_session_memory
Revises: 0015_fiber_metric
Create Date: 2026-05-20 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0016_conversation_session_memory"
down_revision = "0015_fiber_metric"
branch_labels = None
depends_on = None


conversation_message_role = postgresql.ENUM(
    "user",
    "assistant",
    name="conversation_message_role",
    create_type=False,
)


def upgrade() -> None:
    conversation_message_role.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_conversation_sessions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_sessions")),
    )
    op.create_index(op.f("ix_conversation_sessions_user_id"), "conversation_sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_conversation_sessions_started_at"), "conversation_sessions", ["started_at"], unique=False)
    op.create_index(
        op.f("ix_conversation_sessions_last_message_at"),
        "conversation_sessions",
        ["last_message_at"],
        unique=False,
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("role", conversation_message_role, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["conversation_sessions.id"],
            name=op.f("fk_conversation_messages_session_id_conversation_sessions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_messages")),
    )
    op.create_index(op.f("ix_conversation_messages_session_id"), "conversation_messages", ["session_id"], unique=False)
    op.create_index(op.f("ix_conversation_messages_created_at"), "conversation_messages", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_conversation_messages_created_at"), table_name="conversation_messages")
    op.drop_index(op.f("ix_conversation_messages_session_id"), table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index(op.f("ix_conversation_sessions_last_message_at"), table_name="conversation_sessions")
    op.drop_index(op.f("ix_conversation_sessions_started_at"), table_name="conversation_sessions")
    op.drop_index(op.f("ix_conversation_sessions_user_id"), table_name="conversation_sessions")
    op.drop_table("conversation_sessions")
    conversation_message_role.drop(op.get_bind(), checkfirst=True)
