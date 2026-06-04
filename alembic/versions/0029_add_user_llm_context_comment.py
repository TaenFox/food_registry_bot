"""add user llm context comment

Revision ID: 0029_add_user_llm_context_comment
Revises: 0028_add_gastritis_supported_diet
Create Date: 2026-06-04 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_add_user_llm_context_comment"
down_revision = "0028_add_gastritis_supported_diet"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_llm_profiles",
        sa.Column("user_context_comment", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_llm_profiles", "user_context_comment")
