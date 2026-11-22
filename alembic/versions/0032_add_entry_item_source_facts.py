"""Сохранить исходные данные на уровне блюда."""

import sqlalchemy as sa
from alembic import op

revision = "0032_entry_item_source_facts"
down_revision = "0031_add_temporary_internal_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("entry_items", sa.Column("source_facts", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("entry_items", "source_facts")
