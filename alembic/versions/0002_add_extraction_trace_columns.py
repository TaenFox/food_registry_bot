"""add extraction trace columns to entries

Revision ID: 0002_extraction_trace
Revises: 0001_create_core_tables
Create Date: 2026-05-18 20:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_extraction_trace"
down_revision = "0001_create_core_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("entries", sa.Column("extraction_provider", sa.String(length=64), nullable=True))
    op.add_column("entries", sa.Column("extraction_model", sa.String(length=128), nullable=True))
    op.add_column("entries", sa.Column("extraction_raw_payload", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("entries", "extraction_raw_payload")
    op.drop_column("entries", "extraction_model")
    op.drop_column("entries", "extraction_provider")
