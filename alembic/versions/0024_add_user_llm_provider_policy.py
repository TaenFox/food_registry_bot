"""add user llm provider policy

Revision ID: 0024_add_user_llm_provider_policy
Revises: 0023_add_llm_issue_logs
Create Date: 2026-05-26 18:45:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0024_add_user_llm_provider_policy"
down_revision = "0023_add_llm_issue_logs"
branch_labels = None
depends_on = None


account_category_enum = postgresql.ENUM(
    "unassigned",
    "internal",
    "external",
    name="account_category",
    create_type=False,
)
user_llm_selection_mode_enum = postgresql.ENUM(
    "project",
    "personal",
    name="user_llm_selection_mode",
    create_type=False,
)
llm_provider_enum = postgresql.ENUM(
    "openai",
    "mistral",
    name="llm_provider",
    create_type=False,
)
llm_connection_validation_status_enum = postgresql.ENUM(
    "unknown",
    "valid",
    "invalid",
    name="llm_connection_validation_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    account_category_enum.create(bind, checkfirst=True)
    user_llm_selection_mode_enum.create(bind, checkfirst=True)
    llm_provider_enum.create(bind, checkfirst=True)
    llm_connection_validation_status_enum.create(bind, checkfirst=True)

    op.add_column(
        "user_access",
        sa.Column(
            "account_category",
            account_category_enum,
            nullable=False,
            server_default="unassigned",
        ),
    )
    op.execute("UPDATE user_access SET account_category = 'internal' WHERE is_allowed = true")
    op.alter_column("user_access", "account_category", server_default=None)

    op.create_table(
        "user_llm_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("selection_mode", user_llm_selection_mode_enum, nullable=False, server_default="project"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_llm_profiles_user_id", "user_llm_profiles", ["user_id"], unique=True)

    op.create_table(
        "user_llm_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", llm_provider_enum, nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "validation_status",
            llm_connection_validation_status_enum,
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "model", name="uq_user_llm_connections_user_provider_model"),
    )
    op.create_index("ix_user_llm_connections_user_id", "user_llm_connections", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_llm_connections_user_id", table_name="user_llm_connections")
    op.drop_table("user_llm_connections")
    op.drop_index("ix_user_llm_profiles_user_id", table_name="user_llm_profiles")
    op.drop_table("user_llm_profiles")
    op.drop_column("user_access", "account_category")
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=False,
    )

    bind = op.get_bind()
    llm_connection_validation_status_enum.drop(bind, checkfirst=True)
    llm_provider_enum.drop(bind, checkfirst=True)
    user_llm_selection_mode_enum.drop(bind, checkfirst=True)
    account_category_enum.drop(bind, checkfirst=True)
