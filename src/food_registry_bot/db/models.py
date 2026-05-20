from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from food_registry_bot.db.base import Base


def enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class EntryType(str, enum.Enum):
    FOOD = "food"
    WATER = "water"
    WORKOUT = "workout"


class MealType(str, enum.Enum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"
    DRINK = "drink"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    entries: Mapped[list["Entry"]] = relationship(back_populates="user")
    summary_preferences: Mapped[Optional["UserSummaryPreference"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    goal_preferences: Mapped[Optional["UserGoalPreference"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    daily_goal_snapshots: Mapped[list["DailyGoalSnapshot"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserAccess(Base):
    __tablename__ = "user_access"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    is_allowed: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserSummaryPreference(Base):
    __tablename__ = "user_summary_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    show_calories: Mapped[bool] = mapped_column(default=True)
    show_protein: Mapped[bool] = mapped_column(default=True)
    show_fat: Mapped[bool] = mapped_column(default=True)
    show_carbs: Mapped[bool] = mapped_column(default=True)
    show_fiber: Mapped[bool] = mapped_column(default=True)
    show_water: Mapped[bool] = mapped_column(default=True)
    show_post_entry_delta_suffix: Mapped[bool] = mapped_column(default=True)
    summary_display_mode: Mapped[str] = mapped_column(String(16), default="text")
    nutrition_day_start_hour: Mapped[int] = mapped_column(default=4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="summary_preferences")


class UserGoalPreference(Base):
    __tablename__ = "user_goal_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    calorie_goal: Mapped[int] = mapped_column(Integer)
    protein_goal: Mapped[int] = mapped_column(Integer)
    fat_goal: Mapped[int] = mapped_column(Integer)
    carbs_goal: Mapped[int] = mapped_column(Integer)
    fiber_goal: Mapped[int] = mapped_column(Integer)
    water_goal: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="goal_preferences")


class DailyGoalSnapshot(Base):
    __tablename__ = "daily_goal_snapshots"
    __table_args__ = (
        UniqueConstraint("user_id", "summary_date", name="uq_daily_goal_snapshots_user_id_summary_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    summary_date: Mapped[date]
    timezone: Mapped[str] = mapped_column(String(64))
    nutrition_day_start_hour: Mapped[int] = mapped_column(Integer)
    calorie_goal: Mapped[int] = mapped_column(Integer)
    protein_goal: Mapped[int] = mapped_column(Integer)
    fat_goal: Mapped[int] = mapped_column(Integer)
    carbs_goal: Mapped[int] = mapped_column(Integer)
    fiber_goal: Mapped[int] = mapped_column(Integer)
    water_goal: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="daily_goal_snapshots")


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    entry_type: Mapped[EntryType] = mapped_column(
        Enum(EntryType, name="entry_type", values_callable=enum_values)
    )
    meal_type: Mapped[Optional[MealType]] = mapped_column(
        Enum(MealType, name="meal_type", values_callable=enum_values)
    )
    source_text: Mapped[Optional[str]] = mapped_column(Text)
    extraction_provider: Mapped[Optional[str]] = mapped_column(String(64))
    extraction_model: Mapped[Optional[str]] = mapped_column(String(128))
    extraction_raw_payload: Mapped[Optional[str]] = mapped_column(Text)
    llm_comment: Mapped[Optional[str]] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="entries")
    items: Mapped[list["EntryItem"]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
    )


class EntryItem(Base):
    __tablename__ = "entry_items"
    __table_args__ = (
        UniqueConstraint("entry_id", "position", name="uq_entry_items_entry_id_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("entries.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[Optional[int]] = mapped_column(Integer)
    unit: Mapped[Optional[str]] = mapped_column(String(32))
    confidence: Mapped[Optional[str]] = mapped_column(String(32))
    source_type: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    entry: Mapped["Entry"] = relationship(back_populates="items")
    metrics: Mapped[list["EntryItemMetric"]] = relationship(
        back_populates="entry_item",
        cascade="all, delete-orphan",
    )


class SupportedMetric(Base):
    __tablename__ = "supported_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    entry_item_metrics: Mapped[list["EntryItemMetric"]] = relationship(back_populates="metric")


class EntryItemMetric(Base):
    __tablename__ = "entry_item_metrics"
    __table_args__ = (
        UniqueConstraint(
            "entry_item_id",
            "metric_id",
            name="uq_entry_item_metrics_entry_item_id_metric_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_item_id: Mapped[int] = mapped_column(ForeignKey("entry_items.id"), index=True)
    metric_id: Mapped[int] = mapped_column(ForeignKey("supported_metrics.id"), index=True)
    value: Mapped[float]
    confidence: Mapped[str] = mapped_column(String(32), default="medium")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    entry_item: Mapped["EntryItem"] = relationship(back_populates="metrics")
    metric: Mapped["SupportedMetric"] = relationship(back_populates="entry_item_metrics")
