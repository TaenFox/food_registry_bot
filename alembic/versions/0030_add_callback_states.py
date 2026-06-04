"""add callback states

Revision ID: 0030_add_callback_states
Revises: 0029_add_user_llm_context_comment
Create Date: 2026-06-04 21:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0030_add_callback_states"
down_revision = "0029_add_user_llm_context_comment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "callback_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("state_key", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_callback_states_scope"), "callback_states", ["scope"], unique=False)
    op.create_index(op.f("ix_callback_states_state_key"), "callback_states", ["state_key"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_callback_states_state_key"), table_name="callback_states")
    op.drop_index(op.f("ix_callback_states_scope"), table_name="callback_states")
    op.drop_table("callback_states")
