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


class ConversationMessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


class DataExchangeDirection(str, enum.Enum):
    IMPORT = "import"
    EXPORT = "export"


class DataExchangeStatus(str, enum.Enum):
    READY = "ready"
    PROCESSED = "processed"
    ERROR = "error"


class LLMIssueStage(str, enum.Enum):
    EXTRACTION = "extraction"
    NUTRITION = "nutrition"


class AccountCategory(str, enum.Enum):
    UNASSIGNED = "unassigned"
    INTERNAL = "internal"
    EXTERNAL = "external"


class UserLLMSelectionMode(str, enum.Enum):
    PROJECT = "project"
    PERSONAL = "personal"


class LLMProvider(str, enum.Enum):
    OPENAI = "openai"
    MISTRAL = "mistral"


class LLMConnectionValidationStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    VALID = "valid"
    INVALID = "invalid"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    workout_logging_enabled: Mapped[bool] = mapped_column(default=False)
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
    conversation_sessions: Mapped[list["ConversationSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    data_exchange_files: Mapped[list["DataExchangeFile"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    llm_profile: Mapped[Optional["UserLLMProfile"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    llm_connections: Mapped[list["UserLLMConnection"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    diet_preferences: Mapped[list["UserDietPreference"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserAccess(Base):
    __tablename__ = "user_access"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    is_allowed: Mapped[bool] = mapped_column(default=True)
    account_category: Mapped[AccountCategory] = mapped_column(
        Enum(AccountCategory, name="account_category", values_callable=enum_values),
        default=AccountCategory.UNASSIGNED,
    )
    temporary_internal_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserLLMProfile(Base):
    __tablename__ = "user_llm_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    selection_mode: Mapped[UserLLMSelectionMode] = mapped_column(
        Enum(UserLLMSelectionMode, name="user_llm_selection_mode", values_callable=enum_values),
        default=UserLLMSelectionMode.PROJECT,
    )
    user_context_comment: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="llm_profile")


class UserLLMConnection(Base):
    __tablename__ = "user_llm_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "model", name="uq_user_llm_connections_user_provider_model"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[LLMProvider] = mapped_column(
        Enum(LLMProvider, name="llm_provider", values_callable=enum_values)
    )
    model: Mapped[str] = mapped_column(String(128))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(default=True)
    is_selected: Mapped[bool] = mapped_column(default=False)
    validation_status: Mapped[LLMConnectionValidationStatus] = mapped_column(
        Enum(
            LLMConnectionValidationStatus,
            name="llm_connection_validation_status",
            values_callable=enum_values,
        ),
        default=LLMConnectionValidationStatus.UNKNOWN,
    )
    validation_error: Mapped[Optional[str]] = mapped_column(Text)
    last_validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="llm_connections")


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
    show_day_progress_bar: Mapped[bool] = mapped_column(default=False)
    show_post_entry_delta_suffix: Mapped[bool] = mapped_column(default=True)
    summary_display_mode: Mapped[str] = mapped_column(String(16), default="text")
    nutrition_day_start_hour: Mapped[int] = mapped_column(default=4)
    report_goal_tolerance_percent: Mapped[int] = mapped_column(default=10)
    report_noticeable_entry_percentile: Mapped[int] = mapped_column(default=80)
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


class SupportedDiet(Base):
    __tablename__ = "supported_diets"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    is_enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user_preferences: Mapped[list["UserDietPreference"]] = relationship(back_populates="diet")


class UserDietPreference(Base):
    __tablename__ = "user_diet_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "diet_id", name="uq_user_diet_preferences_user_id_diet_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    diet_id: Mapped[int] = mapped_column(ForeignKey("supported_diets.id"), index=True)
    is_enabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="diet_preferences")
    diet: Mapped["SupportedDiet"] = relationship(back_populates="user_preferences")


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


class CallbackState(Base):
    __tablename__ = "callback_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    state_key: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    scope: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class DataExchangeFile(Base):
    __tablename__ = "data_exchange_files"
    __table_args__ = (
        UniqueConstraint("user_id", "direction", "sha256", name="uq_data_exchange_files_user_direction_sha256"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    direction: Mapped[DataExchangeDirection] = mapped_column(
        Enum(DataExchangeDirection, name="data_exchange_direction", values_callable=enum_values)
    )
    status: Mapped[DataExchangeStatus] = mapped_column(
        Enum(DataExchangeStatus, name="data_exchange_status", values_callable=enum_values),
        default=DataExchangeStatus.READY,
    )
    contract_type: Mapped[str] = mapped_column(String(64))
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(512), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    food_entry_count: Mapped[int] = mapped_column(Integer, default=0)
    water_entry_count: Mapped[int] = mapped_column(Integer, default=0)
    date_from: Mapped[Optional[date]]
    date_to: Mapped[Optional[date]]
    validation_message: Mapped[Optional[str]] = mapped_column(Text)
    processing_message: Mapped[Optional[str]] = mapped_column(Text)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="data_exchange_files")


class LLMIssueLog(Base):
    __tablename__ = "llm_issue_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[LLMIssueStage] = mapped_column(
        Enum(LLMIssueStage, name="llm_issue_stage", values_callable=enum_values),
        index=True,
    )
    error_code: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[Optional[str]] = mapped_column(String(64))
    model: Mapped[Optional[str]] = mapped_column(String(128))
    telegram_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    request_text: Mapped[Optional[str]] = mapped_column(Text)
    raw_payload: Mapped[Optional[str]] = mapped_column(Text)
    technical_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    summary_text: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="conversation_sessions")
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("conversation_sessions.id"), index=True)
    role: Mapped[ConversationMessageRole] = mapped_column(
        Enum(ConversationMessageRole, name="conversation_message_role", values_callable=enum_values)
    )
    content: Mapped[str] = mapped_column(Text)
    telegram_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    session: Mapped["ConversationSession"] = relationship(back_populates="messages")
