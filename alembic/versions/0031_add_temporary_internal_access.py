"""add temporary internal access window

Revision ID: 0031_add_temporary_internal_access
Revises: 0030_add_callback_states
Create Date: 2026-06-05 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0031_add_temporary_internal_access"
down_revision = "0030_add_callback_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_access",
        sa.Column("temporary_internal_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_access", "temporary_internal_until")
