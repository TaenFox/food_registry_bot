"""add data exchange files

Revision ID: 0021_data_exchange_files
Revises: 0020_workout_calorie_credit
Create Date: 2026-05-22 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0021_data_exchange_files"
down_revision = "0020_workout_calorie_credit"
branch_labels = None
depends_on = None


data_exchange_direction = postgresql.ENUM("import", "export", name="data_exchange_direction", create_type=False)
data_exchange_status = postgresql.ENUM("ready", "processed", "error", name="data_exchange_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("import", "export", name="data_exchange_direction").create(bind, checkfirst=True)
    postgresql.ENUM("ready", "processed", "error", name="data_exchange_status").create(bind, checkfirst=True)

    op.create_table(
        "data_exchange_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("direction", data_exchange_direction, nullable=False),
        sa.Column("status", data_exchange_status, nullable=False),
        sa.Column("contract_type", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("food_entry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("water_entry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("validation_message", sa.Text(), nullable=True),
        sa.Column("processing_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("storage_path", name="uq_data_exchange_files_storage_path"),
        sa.UniqueConstraint(
            "user_id",
            "direction",
            "sha256",
            name="uq_data_exchange_files_user_direction_sha256",
        ),
    )
    op.create_index("ix_data_exchange_files_user_id", "data_exchange_files", ["user_id"])
    op.create_index("ix_data_exchange_files_sha256", "data_exchange_files", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_data_exchange_files_sha256", table_name="data_exchange_files")
    op.drop_index("ix_data_exchange_files_user_id", table_name="data_exchange_files")
    op.drop_table("data_exchange_files")
    bind = op.get_bind()
    postgresql.ENUM("ready", "processed", "error", name="data_exchange_status").drop(bind, checkfirst=True)
    postgresql.ENUM("import", "export", name="data_exchange_direction").drop(bind, checkfirst=True)
