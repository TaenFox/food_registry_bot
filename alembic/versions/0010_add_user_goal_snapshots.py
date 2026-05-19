"""add user goal snapshots

Revision ID: 0010_user_goal_snapshots
Revises: 0009_nutrition_day_start_hour
Create Date: 2026-05-19 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0010_user_goal_snapshots"
down_revision = "0009_nutrition_day_start_hour"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_goal_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("calorie_goal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_goal_preferences_user_id"),
        "user_goal_preferences",
        ["user_id"],
        unique=True,
    )

    op.create_table(
        "daily_goal_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("summary_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("nutrition_day_start_hour", sa.Integer(), nullable=False),
        sa.Column("calorie_goal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "summary_date", name="uq_daily_goal_snapshots_user_id_summary_date"),
    )
    op.create_index(
        op.f("ix_daily_goal_snapshots_user_id"),
        "daily_goal_snapshots",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_daily_goal_snapshots_user_id"), table_name="daily_goal_snapshots")
    op.drop_table("daily_goal_snapshots")
    op.drop_index(op.f("ix_user_goal_preferences_user_id"), table_name="user_goal_preferences")
    op.drop_table("user_goal_preferences")
