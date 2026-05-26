"""add llm issue logs

Revision ID: 0023_add_llm_issue_logs
Revises: 0022_add_period_report_preferences
Create Date: 2026-05-26 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0023_add_llm_issue_logs"
down_revision = "0022_period_report_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_issue_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "stage",
            sa.Enum("extraction", "nutrition", name="llm_issue_stage"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("request_text", sa.Text(), nullable=True),
        sa.Column("raw_payload", sa.Text(), nullable=True),
        sa.Column("technical_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_issue_logs_created_at"), "llm_issue_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_llm_issue_logs_error_code"), "llm_issue_logs", ["error_code"], unique=False)
    op.create_index(op.f("ix_llm_issue_logs_stage"), "llm_issue_logs", ["stage"], unique=False)
    op.create_index(
        op.f("ix_llm_issue_logs_telegram_user_id"),
        "llm_issue_logs",
        ["telegram_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_issue_logs_telegram_user_id"), table_name="llm_issue_logs")
    op.drop_index(op.f("ix_llm_issue_logs_stage"), table_name="llm_issue_logs")
    op.drop_index(op.f("ix_llm_issue_logs_error_code"), table_name="llm_issue_logs")
    op.drop_index(op.f("ix_llm_issue_logs_created_at"), table_name="llm_issue_logs")
    op.drop_table("llm_issue_logs")
    sa.Enum(name="llm_issue_stage").drop(op.get_bind(), checkfirst=False)
