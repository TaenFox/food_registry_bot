from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from food_registry_bot.db.models import (
    AccountCategory,
    CallbackState,
    Entry,
    EntryItem,
    EntryItemMetric,
    EntryType,
    LLMConnectionValidationStatus,
    LLMProvider,
    LLMIssueLog,
    LLMIssueStage,
    MealType,
    DataExchangeDirection,
    DataExchangeFile,
    DataExchangeStatus,
    SupportedDiet,
    SupportedMetric,
    DailyGoalSnapshot,
    ConversationMessage,
    ConversationMessageRole,
    ConversationSession,
    User,
    UserAccess,
    UserLLMConnection,
    UserLLMProfile,
    UserLLMSelectionMode,
    UserGoalPreference,
    UserDietPreference,
    UserSummaryPreference,
)
from food_registry_bot.llm_access.crypto import SecretCipher, SecretCipherError
if TYPE_CHECKING:
    from food_registry_bot.nutrition.journal_adapter import PreparedNutritionRequest, ResolvedNutritionEstimate

SUPPORTED_NUTRITION_DAY_START_HOURS = (0, 2, 4, 6)
SUPPORTED_SUMMARY_DISPLAY_MODES = ("text", "bars")
SUPPORTED_REPORT_GOAL_TOLERANCE_PERCENTS = (5, 10, 15, 20)
SUPPORTED_REPORT_NOTICEABLE_ENTRY_PERCENTILES = (70, 75, 80, 85, 90, 95)
SUPPORTED_GOAL_METRIC_CODES = ("calories", "protein", "fat", "carbs", "fiber", "water")
DEFAULT_DAILY_GOALS = {
    "calories": 1800,
    "protein": 90,
    "fat": 60,
    "carbs": 210,
    "fiber": 25,
    "water": 2000,
}
USER_CONTEXT_COMMENT_ENCRYPTED_PREFIX = "enc:"


@dataclass(frozen=True)
class EntryItemCreate:
    name: str
    quantity: int | None = None
    unit: str | None = None
    confidence: str | None = None
    source_type: str | None = None


@dataclass(frozen=True)
class EntryItemMetricValue:
    code: str
    value: float
    confidence: str


@dataclass(frozen=True)
class CallbackStatePayload:
    scope: str
    values: dict[str, object]


@dataclass(frozen=True)
class KnownUserAccessView:
    telegram_user_id: int
    username: str | None
    has_profile: bool
    is_allowed: bool
    account_category: str


@dataclass(frozen=True)
class ConversationTurn:
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class LLMIssueLogCreate:
    stage: LLMIssueStage
    error_code: str
    provider: str | None = None
    model: str | None = None
    telegram_user_id: int | None = None
    username: str | None = None
    request_text: str | None = None
    raw_payload: str | None = None
    technical_message: str | None = None


@dataclass(frozen=True)
class UserLLMConnectionView:
    provider: str
    model: str
    is_enabled: bool
    is_selected: bool
    validation_status: str
    validation_error: str | None
    has_encrypted_api_key: bool


@dataclass(frozen=True)
class SupportedDietView:
    code: str
    name: str
    is_enabled: bool
    is_selected: bool


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_telegram_user_id(self, telegram_user_id: int) -> Optional[User]:
        statement = select(User).where(User.telegram_user_id == telegram_user_id)
        return self._session.scalar(statement)

    def create(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        timezone: str = "Europe/Moscow",
        workout_logging_enabled: bool = False,
    ) -> User:
        user = User(
            telegram_user_id=telegram_user_id,
            username=username,
            timezone=timezone,
            workout_logging_enabled=workout_logging_enabled,
        )
        self._session.add(user)
        self._session.flush()
        return user

    def get_or_create(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        timezone: str = "Europe/Moscow",
        workout_logging_enabled: bool = False,
    ) -> tuple[User, bool]:
        user = self.get_by_telegram_user_id(telegram_user_id)
        if user is not None:
            return user, False

        user = self.create(
            telegram_user_id=telegram_user_id,
            username=username,
            timezone=timezone,
            workout_logging_enabled=workout_logging_enabled,
        )
        return user, True

    def toggle_workout_logging_enabled(self, *, user_id: int) -> User:
        user = self._session.get(User, user_id)
        if user is None:
            raise ValueError(f"User {user_id} was not found")

        user.workout_logging_enabled = not user.workout_logging_enabled
        self._session.flush()
        return user


class UserAccessRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_telegram_user_id(self, telegram_user_id: int) -> Optional[UserAccess]:
        statement = select(UserAccess).where(UserAccess.telegram_user_id == telegram_user_id)
        return self._session.scalar(statement)

    def is_allowed(self, telegram_user_id: int) -> bool:
        access = self.get_by_telegram_user_id(telegram_user_id)
        return bool(access and access.is_allowed)

    def get_account_category(self, telegram_user_id: int) -> AccountCategory:
        access = self.get_by_telegram_user_id(telegram_user_id)
        if access is None:
            return AccountCategory.UNASSIGNED
        return access.account_category

    def set_access(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        is_allowed: bool,
        account_category: AccountCategory | None = None,
    ) -> UserAccess:
        access = self.get_by_telegram_user_id(telegram_user_id)
        if access is None:
            access = UserAccess(
                telegram_user_id=telegram_user_id,
                username=username,
                is_allowed=is_allowed,
                account_category=account_category or (
                    AccountCategory.EXTERNAL if is_allowed else AccountCategory.UNASSIGNED
                ),
            )
            self._session.add(access)
        else:
            access.username = username
            access.is_allowed = is_allowed
            if account_category is not None:
                access.account_category = account_category
            elif is_allowed and access.account_category is AccountCategory.UNASSIGNED:
                access.account_category = AccountCategory.EXTERNAL

        self._session.flush()
        return access

    def set_account_category(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        account_category: AccountCategory,
    ) -> UserAccess:
        access = self.get_by_telegram_user_id(telegram_user_id)
        if access is None:
            access = UserAccess(
                telegram_user_id=telegram_user_id,
                username=username,
                is_allowed=account_category is not AccountCategory.UNASSIGNED,
                account_category=account_category,
            )
            self._session.add(access)
        else:
            access.username = username
            access.account_category = account_category
            if account_category is AccountCategory.UNASSIGNED:
                access.is_allowed = False

        self._session.flush()
        return access

    def list_known_users(self) -> list[KnownUserAccessView]:
        users = list(self._session.scalars(select(User).order_by(User.telegram_user_id.asc())))
        access_rows = list(
            self._session.scalars(select(UserAccess).order_by(UserAccess.telegram_user_id.asc()))
        )

        users_by_telegram_id = {user.telegram_user_id: user for user in users}
        access_by_telegram_id = {
            access.telegram_user_id: access for access in access_rows
        }

        known_ids = sorted(set(users_by_telegram_id) | set(access_by_telegram_id))
        result: list[KnownUserAccessView] = []
        for telegram_user_id in known_ids:
            user = users_by_telegram_id.get(telegram_user_id)
            access = access_by_telegram_id.get(telegram_user_id)
            result.append(
                KnownUserAccessView(
                    telegram_user_id=telegram_user_id,
                    username=(user.username if user is not None else None) or (
                        access.username if access is not None else None
                    ),
                    has_profile=user is not None,
                    is_allowed=bool(access and access.is_allowed),
                    account_category=(
                        access.account_category.value
                        if access is not None
                        else AccountCategory.UNASSIGNED.value
                    ),
                )
            )

        return result


class UserLLMProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_user_id(self, user_id: int) -> Optional[UserLLMProfile]:
        statement = select(UserLLMProfile).where(UserLLMProfile.user_id == user_id)
        return self._session.scalar(statement)

    def get_or_create(self, *, user_id: int) -> tuple[UserLLMProfile, bool]:
        profile = self.get_by_user_id(user_id)
        if profile is not None:
            return profile, False

        profile = UserLLMProfile(user_id=user_id, selection_mode=UserLLMSelectionMode.PROJECT)
        self._session.add(profile)
        self._session.flush()
        return profile, True

    def set_selection_mode(
        self,
        *,
        user_id: int,
        selection_mode: UserLLMSelectionMode,
    ) -> UserLLMProfile:
        profile, _created = self.get_or_create(user_id=user_id)
        profile.selection_mode = selection_mode
        self._session.flush()
        return profile

    def set_user_context_comment(
        self,
        *,
        user_id: int,
        user_context_comment: str | None,
        encryption_secret: str,
    ) -> UserLLMProfile:
        profile, _created = self.get_or_create(user_id=user_id)
        profile.user_context_comment = self._serialize_user_context_comment(
            user_context_comment=user_context_comment,
            encryption_secret=encryption_secret,
        )
        self._session.flush()
        return profile

    def get_user_context_comment(
        self,
        *,
        user_id: int,
        encryption_secret: str | None,
    ) -> str | None:
        profile, _created = self.get_or_create(user_id=user_id)
        return self._deserialize_user_context_comment(
            stored_value=profile.user_context_comment,
            encryption_secret=encryption_secret,
        )

    @staticmethod
    def _serialize_user_context_comment(
        *,
        user_context_comment: str | None,
        encryption_secret: str,
    ) -> str | None:
        if user_context_comment is None:
            return None
        return USER_CONTEXT_COMMENT_ENCRYPTED_PREFIX + SecretCipher(encryption_secret).encrypt(user_context_comment)

    @staticmethod
    def _deserialize_user_context_comment(
        *,
        stored_value: str | None,
        encryption_secret: str | None,
    ) -> str | None:
        if stored_value is None:
            return None
        if not stored_value.startswith(USER_CONTEXT_COMMENT_ENCRYPTED_PREFIX):
            return stored_value
        if encryption_secret is None or not encryption_secret.strip():
            raise ValueError("A non-empty secret is required for user context decryption")
        encrypted_payload = stored_value.removeprefix(USER_CONTEXT_COMMENT_ENCRYPTED_PREFIX)
        try:
            return SecretCipher(encryption_secret).decrypt(encrypted_payload)
        except SecretCipherError as exc:
            raise ValueError("User context decryption failed") from exc


class UserLLMConnectionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_user_provider_model(
        self,
        *,
        user_id: int,
        provider: LLMProvider,
        model: str,
    ) -> Optional[UserLLMConnection]:
        statement = select(UserLLMConnection).where(
            UserLLMConnection.user_id == user_id,
            UserLLMConnection.provider == provider,
            UserLLMConnection.model == model,
        )
        return self._session.scalar(statement)

    def list_for_user(self, *, user_id: int) -> list[UserLLMConnection]:
        statement = (
            select(UserLLMConnection)
            .where(UserLLMConnection.user_id == user_id)
            .order_by(UserLLMConnection.provider.asc(), UserLLMConnection.model.asc())
        )
        return list(self._session.scalars(statement))

    def list_views_for_user(self, *, user_id: int) -> list[UserLLMConnectionView]:
        return [
            UserLLMConnectionView(
                provider=connection.provider.value,
                model=connection.model,
                is_enabled=connection.is_enabled,
                is_selected=connection.is_selected,
                validation_status=connection.validation_status.value,
                validation_error=connection.validation_error,
                has_encrypted_api_key=bool(connection.encrypted_api_key),
            )
            for connection in self.list_for_user(user_id=user_id)
        ]

    def upsert_connection(
        self,
        *,
        user_id: int,
        provider: LLMProvider,
        model: str,
        encrypted_api_key: str,
        is_enabled: bool = True,
        is_selected: bool = False,
        validation_status: LLMConnectionValidationStatus = LLMConnectionValidationStatus.UNKNOWN,
        validation_error: str | None = None,
        last_validated_at: datetime | None = None,
    ) -> UserLLMConnection:
        connection = self.get_by_user_provider_model(
            user_id=user_id,
            provider=provider,
            model=model,
        )
        if connection is None:
            connection = UserLLMConnection(
                user_id=user_id,
                provider=provider,
                model=model,
                encrypted_api_key=encrypted_api_key,
                is_enabled=is_enabled,
                is_selected=is_selected,
                validation_status=validation_status,
                validation_error=validation_error,
                last_validated_at=last_validated_at,
            )
            self._session.add(connection)
        else:
            connection.encrypted_api_key = encrypted_api_key
            connection.is_enabled = is_enabled
            connection.is_selected = is_selected
            connection.validation_status = validation_status
            connection.validation_error = validation_error
            connection.last_validated_at = last_validated_at

        if is_selected:
            self._clear_selected_for_user_except(user_id=user_id, keep_connection=connection)

        self._session.flush()
        return connection

    def select_provider(self, *, user_id: int, provider: LLMProvider) -> list[UserLLMConnection]:
        connections = self.list_for_user(user_id=user_id)
        updated_connections: list[UserLLMConnection] = []
        for connection in connections:
            should_select = connection.provider is provider and connection.is_enabled
            connection.is_selected = should_select
            if should_select:
                updated_connections.append(connection)

        self._session.flush()
        return updated_connections

    def select_connection(
        self,
        *,
        user_id: int,
        provider: LLMProvider,
        model: str,
    ) -> UserLLMConnection | None:
        target_connection = self.get_by_user_provider_model(
            user_id=user_id,
            provider=provider,
            model=model,
        )
        if target_connection is None or not target_connection.is_enabled:
            return None

        for connection in self.list_for_user(user_id=user_id):
            connection.is_selected = connection.id == target_connection.id

        self._session.flush()
        return target_connection

    def get_selected_for_user_provider_model(
        self,
        *,
        user_id: int,
        provider: LLMProvider,
        model: str,
    ) -> Optional[UserLLMConnection]:
        statement = select(UserLLMConnection).where(
            UserLLMConnection.user_id == user_id,
            UserLLMConnection.provider == provider,
            UserLLMConnection.model == model,
            UserLLMConnection.is_selected.is_(True),
            UserLLMConnection.is_enabled.is_(True),
        )
        return self._session.scalar(statement)

    def get_selected_for_user(self, *, user_id: int) -> Optional[UserLLMConnection]:
        statement = select(UserLLMConnection).where(
            UserLLMConnection.user_id == user_id,
            UserLLMConnection.is_selected.is_(True),
            UserLLMConnection.is_enabled.is_(True),
        )
        return self._session.scalar(statement)

    def delete_connection(
        self,
        *,
        user_id: int,
        provider: LLMProvider,
        model: str,
    ) -> bool:
        connection = self.get_by_user_provider_model(
            user_id=user_id,
            provider=provider,
            model=model,
        )
        if connection is None:
            return False

        self._session.delete(connection)
        self._session.flush()
        return True

    def _clear_selected_for_user_except(
        self,
        *,
        user_id: int,
        keep_connection: UserLLMConnection,
    ) -> None:
        for connection in self.list_for_user(user_id=user_id):
            if connection.id == keep_connection.id:
                continue
            if connection.provider is keep_connection.provider:
                continue
            connection.is_selected = False


class UserSummaryPreferenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_user_id(self, user_id: int) -> Optional[UserSummaryPreference]:
        statement = select(UserSummaryPreference).where(UserSummaryPreference.user_id == user_id)
        return self._session.scalar(statement)

    def get_or_create(self, *, user_id: int) -> tuple[UserSummaryPreference, bool]:
        preference = self.get_by_user_id(user_id)
        if preference is not None:
            return preference, False

        preference = UserSummaryPreference(
            user_id=user_id,
            show_calories=True,
            show_protein=True,
            show_fat=True,
            show_carbs=True,
            show_fiber=True,
            show_water=True,
            show_day_progress_bar=False,
            show_post_entry_delta_suffix=True,
            summary_display_mode="text",
            nutrition_day_start_hour=4,
            report_goal_tolerance_percent=10,
            report_noticeable_entry_percentile=80,
        )
        self._session.add(preference)
        self._session.flush()
        return preference, True

    def set_metric_visibility(
        self,
        *,
        user_id: int,
        metric_code: str,
        is_visible: bool,
    ) -> UserSummaryPreference:
        preference, _created = self.get_or_create(user_id=user_id)
        setattr(preference, self._resolve_metric_attribute(metric_code), is_visible)
        self._session.flush()
        return preference

    def toggle_metric_visibility(
        self,
        *,
        user_id: int,
        metric_code: str,
    ) -> UserSummaryPreference:
        preference, _created = self.get_or_create(user_id=user_id)
        attribute_name = self._resolve_metric_attribute(metric_code)
        setattr(preference, attribute_name, not getattr(preference, attribute_name))
        self._session.flush()
        return preference

    def cycle_nutrition_day_start_hour(self, *, user_id: int) -> UserSummaryPreference:
        preference, _created = self.get_or_create(user_id=user_id)
        current_index = SUPPORTED_NUTRITION_DAY_START_HOURS.index(preference.nutrition_day_start_hour)
        next_index = (current_index + 1) % len(SUPPORTED_NUTRITION_DAY_START_HOURS)
        preference.nutrition_day_start_hour = SUPPORTED_NUTRITION_DAY_START_HOURS[next_index]
        self._session.flush()
        return preference

    def cycle_summary_display_mode(self, *, user_id: int) -> UserSummaryPreference:
        preference, _created = self.get_or_create(user_id=user_id)
        current_index = SUPPORTED_SUMMARY_DISPLAY_MODES.index(preference.summary_display_mode)
        next_index = (current_index + 1) % len(SUPPORTED_SUMMARY_DISPLAY_MODES)
        preference.summary_display_mode = SUPPORTED_SUMMARY_DISPLAY_MODES[next_index]
        self._session.flush()
        return preference

    def toggle_post_entry_delta_suffix(self, *, user_id: int) -> UserSummaryPreference:
        preference, _created = self.get_or_create(user_id=user_id)
        preference.show_post_entry_delta_suffix = not preference.show_post_entry_delta_suffix
        self._session.flush()
        return preference

    def toggle_day_progress_bar(self, *, user_id: int) -> UserSummaryPreference:
        preference, _created = self.get_or_create(user_id=user_id)
        preference.show_day_progress_bar = not preference.show_day_progress_bar
        self._session.flush()
        return preference

    def cycle_report_goal_tolerance_percent(self, *, user_id: int) -> UserSummaryPreference:
        preference, _created = self.get_or_create(user_id=user_id)
        current_index = SUPPORTED_REPORT_GOAL_TOLERANCE_PERCENTS.index(preference.report_goal_tolerance_percent)
        next_index = (current_index + 1) % len(SUPPORTED_REPORT_GOAL_TOLERANCE_PERCENTS)
        preference.report_goal_tolerance_percent = SUPPORTED_REPORT_GOAL_TOLERANCE_PERCENTS[next_index]
        self._session.flush()
        return preference

    def cycle_report_noticeable_entry_percentile(self, *, user_id: int) -> UserSummaryPreference:
        preference, _created = self.get_or_create(user_id=user_id)
        current_index = SUPPORTED_REPORT_NOTICEABLE_ENTRY_PERCENTILES.index(preference.report_noticeable_entry_percentile)
        next_index = (current_index + 1) % len(SUPPORTED_REPORT_NOTICEABLE_ENTRY_PERCENTILES)
        preference.report_noticeable_entry_percentile = SUPPORTED_REPORT_NOTICEABLE_ENTRY_PERCENTILES[next_index]
        self._session.flush()
        return preference

    @staticmethod
    def _resolve_metric_attribute(metric_code: str) -> str:
        metric_attributes = {
            "calories": "show_calories",
            "protein": "show_protein",
            "fat": "show_fat",
            "carbs": "show_carbs",
            "fiber": "show_fiber",
            "water": "show_water",
        }
        try:
            return metric_attributes[metric_code]
        except KeyError as exc:
            raise ValueError(f"Unsupported summary preference metric code: {metric_code}") from exc


class UserGoalPreferenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_user_id(self, user_id: int) -> Optional[UserGoalPreference]:
        statement = select(UserGoalPreference).where(UserGoalPreference.user_id == user_id)
        return self._session.scalar(statement)

    def get_or_create(self, *, user_id: int) -> tuple[UserGoalPreference, bool]:
        preference = self.get_by_user_id(user_id)
        if preference is not None:
            return preference, False

        preference = UserGoalPreference(
            user_id=user_id,
            calorie_goal=DEFAULT_DAILY_GOALS["calories"],
            protein_goal=DEFAULT_DAILY_GOALS["protein"],
            fat_goal=DEFAULT_DAILY_GOALS["fat"],
            carbs_goal=DEFAULT_DAILY_GOALS["carbs"],
            fiber_goal=DEFAULT_DAILY_GOALS["fiber"],
            water_goal=DEFAULT_DAILY_GOALS["water"],
        )
        self._session.add(preference)
        self._session.flush()
        return preference, True

    def set_goal(
        self,
        *,
        user_id: int,
        metric_code: str,
        goal_value: int,
    ) -> UserGoalPreference:
        if goal_value <= 0:
            raise ValueError("goal_value must be positive")

        preference, _created = self.get_or_create(user_id=user_id)
        setattr(preference, self._resolve_goal_attribute(metric_code), goal_value)
        self._session.flush()
        return preference

    @staticmethod
    def _resolve_goal_attribute(metric_code: str) -> str:
        attribute_names = {
            "calories": "calorie_goal",
            "protein": "protein_goal",
            "fat": "fat_goal",
            "carbs": "carbs_goal",
            "fiber": "fiber_goal",
            "water": "water_goal",
        }
        try:
            return attribute_names[metric_code]
        except KeyError as exc:
            raise ValueError(f"Unsupported goal metric code: {metric_code}") from exc


class DailyGoalSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_user_id_and_date(
        self,
        *,
        user_id: int,
        summary_date: date,
    ) -> Optional[DailyGoalSnapshot]:
        statement = select(DailyGoalSnapshot).where(
            DailyGoalSnapshot.user_id == user_id,
            DailyGoalSnapshot.summary_date == summary_date,
        )
        return self._session.scalar(statement)

    def create(
        self,
        *,
        user_id: int,
        summary_date: date,
        timezone_name: str,
        nutrition_day_start_hour: int,
        calorie_goal: int,
        protein_goal: int,
        fat_goal: int,
        carbs_goal: int,
        fiber_goal: int,
        water_goal: int,
    ) -> DailyGoalSnapshot:
        snapshot = DailyGoalSnapshot(
            user_id=user_id,
            summary_date=summary_date,
            timezone=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
            calorie_goal=calorie_goal,
            protein_goal=protein_goal,
            fat_goal=fat_goal,
            carbs_goal=carbs_goal,
            fiber_goal=fiber_goal,
            water_goal=water_goal,
        )
        self._session.add(snapshot)
        self._session.flush()
        return snapshot


class EntryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        user_id: int,
        entry_type: EntryType,
        occurred_at: datetime,
        meal_type: MealType | None = None,
        source_text: str | None = None,
        extraction_provider: str | None = None,
        extraction_model: str | None = None,
        extraction_raw_payload: str | None = None,
        llm_comment: str | None = None,
        items: list[EntryItemCreate] | None = None,
    ) -> Entry:
        entry = Entry(
            user_id=user_id,
            entry_type=entry_type,
            meal_type=meal_type,
            source_text=source_text,
            extraction_provider=extraction_provider,
            extraction_model=extraction_model,
            extraction_raw_payload=extraction_raw_payload,
            llm_comment=llm_comment,
            occurred_at=occurred_at,
        )
        self._session.add(entry)
        self._session.flush()

        for position, item in enumerate(items or []):
            self._session.add(
                EntryItem(
                    entry_id=entry.id,
                    position=position,
                    name=item.name,
                    quantity=item.quantity,
                    unit=item.unit,
                    confidence=item.confidence,
                    source_type=item.source_type,
                )
            )

        self._session.flush()
        return entry

    def list_recent_for_user(self, *, user_id: int, limit: int = 5, offset: int = 0) -> list[Entry]:
        statement = (
            select(Entry)
            .where(Entry.user_id == user_id)
            .options(
                selectinload(Entry.items)
                .selectinload(EntryItem.metrics)
                .selectinload(EntryItemMetric.metric)
            )
            .order_by(Entry.occurred_at.desc(), Entry.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(statement))

    def list_by_ids(self, *, entry_ids: list[int]) -> list[Entry]:
        if not entry_ids:
            return []

        statement = (
            select(Entry)
            .where(Entry.id.in_(entry_ids))
            .options(selectinload(Entry.items))
            .order_by(Entry.id.asc())
        )
        return list(self._session.scalars(statement))


    def get_by_id_for_user(self, *, entry_id: int, user_id: int) -> Optional[Entry]:
        statement = (
            select(Entry)
            .where(Entry.id == entry_id, Entry.user_id == user_id)
            .options(
                selectinload(Entry.items)
                .selectinload(EntryItem.metrics)
                .selectinload(EntryItemMetric.metric)
            )
        )
        return self._session.scalar(statement)

    def delete(self, entry: Entry) -> None:
        self._session.delete(entry)
        self._session.flush()

    def delete_all_for_user(self, *, user_id: int) -> int:
        entries = list(
            self._session.scalars(
                select(Entry)
                .where(Entry.user_id == user_id)
                .options(selectinload(Entry.items))
                .order_by(Entry.id.asc())
            )
        )
        deleted_count = len(entries)
        for entry in entries:
            self._session.delete(entry)
        self._session.flush()
        return deleted_count

    def list_food_for_user_between(
        self,
        *,
        user_id: int,
        occurred_at_from: datetime,
        occurred_at_to: datetime,
    ) -> list[Entry]:
        statement = (
            select(Entry)
            .where(
                Entry.user_id == user_id,
                Entry.entry_type == EntryType.FOOD,
                Entry.occurred_at >= occurred_at_from,
                Entry.occurred_at < occurred_at_to,
            )
            .options(
                selectinload(Entry.items)
                .selectinload(EntryItem.metrics)
                .selectinload(EntryItemMetric.metric)
            )
            .order_by(Entry.occurred_at.asc(), Entry.id.asc())
        )
        return list(self._session.scalars(statement))

    def list_water_for_user_between(
        self,
        *,
        user_id: int,
        occurred_at_from: datetime,
        occurred_at_to: datetime,
    ) -> list[Entry]:
        statement = (
            select(Entry)
            .where(
                Entry.user_id == user_id,
                Entry.entry_type == EntryType.WATER,
                Entry.occurred_at >= occurred_at_from,
                Entry.occurred_at < occurred_at_to,
            )
            .options(
                selectinload(Entry.items)
                .selectinload(EntryItem.metrics)
                .selectinload(EntryItemMetric.metric)
            )
            .order_by(Entry.occurred_at.asc(), Entry.id.asc())
        )
        return list(self._session.scalars(statement))

    def list_workout_for_user_between(
        self,
        *,
        user_id: int,
        occurred_at_from: datetime,
        occurred_at_to: datetime,
    ) -> list[Entry]:
        statement = (
            select(Entry)
            .where(
                Entry.user_id == user_id,
                Entry.entry_type == EntryType.WORKOUT,
                Entry.occurred_at >= occurred_at_from,
                Entry.occurred_at < occurred_at_to,
            )
            .options(
                selectinload(Entry.items)
                .selectinload(EntryItem.metrics)
                .selectinload(EntryItemMetric.metric)
            )
            .order_by(Entry.occurred_at.asc(), Entry.id.asc())
        )
        return list(self._session.scalars(statement))

    def list_incomplete_food_entry_ids(
        self,
        *,
        required_metric_codes: list[str],
        limit: int,
    ) -> list[int]:
        if limit <= 0:
            return []

        statement = (
            select(Entry)
            .where(Entry.entry_type == EntryType.FOOD)
            .options(
                selectinload(Entry.items)
                .selectinload(EntryItem.metrics)
                .selectinload(EntryItemMetric.metric)
            )
            .order_by(Entry.occurred_at.asc(), Entry.id.asc())
        )

        incomplete_entry_ids: list[int] = []
        required_metric_code_set = set(required_metric_codes)
        for entry in self._session.scalars(statement):
            is_incomplete = False
            for item in entry.items:
                item_metric_codes = {
                    metric.metric.code
                    for metric in item.metrics
                    if metric.metric is not None
                }
                if not required_metric_code_set.issubset(item_metric_codes):
                    is_incomplete = True
                    break

            if is_incomplete:
                incomplete_entry_ids.append(entry.id)
                if len(incomplete_entry_ids) >= limit:
                    break

        return incomplete_entry_ids

    def count_incomplete_food_entries(
        self,
        *,
        required_metric_codes: list[str],
    ) -> int:
        statement = (
            select(Entry)
            .where(Entry.entry_type == EntryType.FOOD)
            .options(
                selectinload(Entry.items)
                .selectinload(EntryItem.metrics)
                .selectinload(EntryItemMetric.metric)
            )
        )

        required_metric_code_set = set(required_metric_codes)
        count = 0
        for entry in self._session.scalars(statement):
            for item in entry.items:
                item_metric_codes = {
                    metric.metric.code
                    for metric in item.metrics
                    if metric.metric is not None
                }
                if not required_metric_code_set.issubset(item_metric_codes):
                    count += 1
                    break

        return count


class CallbackStateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        scope: str,
        values: dict[str, object],
    ) -> CallbackState:
        state = CallbackState(
            state_key=secrets.token_hex(5),
            scope=scope,
            payload_json=json.dumps(values, ensure_ascii=True, separators=(",", ":")),
        )
        self._session.add(state)
        self._session.flush()
        return state

    def get_payload(
        self,
        *,
        scope: str,
        state_key: str,
    ) -> CallbackStatePayload | None:
        statement = select(CallbackState).where(
            CallbackState.scope == scope,
            CallbackState.state_key == state_key,
        )
        state = self._session.scalar(statement)
        if state is None:
            return None
        return CallbackStatePayload(
            scope=state.scope,
            values=json.loads(state.payload_json),
        )


class ConversationSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_active_for_user(
        self,
        *,
        user_id: int,
        reference_at: datetime,
        ttl_minutes: int = 60,
    ) -> Optional[ConversationSession]:
        normalized_reference_at = (
            reference_at if reference_at.tzinfo is not None else reference_at.replace(tzinfo=timezone.utc)
        )
        cutoff = normalized_reference_at - timedelta(minutes=ttl_minutes)
        statement = (
            select(ConversationSession)
            .where(
                ConversationSession.user_id == user_id,
                ConversationSession.last_message_at >= cutoff,
            )
            .order_by(ConversationSession.last_message_at.desc(), ConversationSession.id.desc())
            .limit(1)
        )
        return self._session.scalar(statement)

    def create(
        self,
        *,
        user_id: int,
        started_at: datetime,
        summary_text: str | None = None,
    ) -> ConversationSession:
        conversation_session = ConversationSession(
            user_id=user_id,
            summary_text=summary_text,
            started_at=started_at,
            last_message_at=started_at,
        )
        self._session.add(conversation_session)
        self._session.flush()
        return conversation_session

    def create_or_get_active(
        self,
        *,
        user_id: int,
        reference_at: datetime,
        ttl_minutes: int = 60,
    ) -> ConversationSession:
        active_session = self.get_active_for_user(
            user_id=user_id,
            reference_at=reference_at,
            ttl_minutes=ttl_minutes,
        )
        if active_session is not None:
            return active_session
        return self.create(user_id=user_id, started_at=reference_at)

    def update_summary_and_touch(
        self,
        *,
        session_id: int,
        summary_text: str | None,
        last_message_at: datetime,
    ) -> ConversationSession:
        conversation_session = self._session.get(ConversationSession, session_id)
        if conversation_session is None:
            raise ValueError(f"Conversation session {session_id} was not found")
        conversation_session.summary_text = summary_text
        conversation_session.last_message_at = last_message_at
        self._session.flush()
        return conversation_session


class ConversationMessageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        session_id: int,
        role: ConversationMessageRole,
        content: str,
        created_at: datetime,
        telegram_chat_id: int | None = None,
        telegram_message_id: int | None = None,
    ) -> ConversationMessage:
        message = ConversationMessage(
            session_id=session_id,
            role=role,
            content=content,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            created_at=created_at,
        )
        self._session.add(message)
        self._session.flush()
        return message

    def get_session_by_assistant_message(
        self,
        *,
        telegram_chat_id: int,
        telegram_message_id: int,
    ) -> ConversationSession | None:
        statement = (
            select(ConversationSession)
            .join(ConversationMessage, ConversationMessage.session_id == ConversationSession.id)
            .where(
                ConversationMessage.role == ConversationMessageRole.ASSISTANT,
                ConversationMessage.telegram_chat_id == telegram_chat_id,
                ConversationMessage.telegram_message_id == telegram_message_id,
            )
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(1)
        )
        return self._session.scalar(statement)

    def list_recent_for_session(
        self,
        *,
        session_id: int,
        limit: int = 6,
    ) -> list[ConversationTurn]:
        statement = (
            select(ConversationMessage)
            .where(ConversationMessage.session_id == session_id)
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(limit)
        )
        rows = list(self._session.scalars(statement))
        rows.reverse()
        return [
            ConversationTurn(
                role=row.role.value,
                content=row.content,
                created_at=row.created_at,
            )
            for row in rows
        ]


class SupportedMetricRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_all(self) -> list[SupportedMetric]:
        statement = select(SupportedMetric).order_by(SupportedMetric.id.asc())
        return list(self._session.scalars(statement))

    def get_by_code_map(self, codes: list[str]) -> dict[str, SupportedMetric]:
        statement = select(SupportedMetric).where(SupportedMetric.code.in_(codes))
        metrics = list(self._session.scalars(statement))
        return {metric.code: metric for metric in metrics}


class SupportedDietRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_all(self) -> list[SupportedDiet]:
        statement = select(SupportedDiet).order_by(SupportedDiet.id.asc())
        return list(self._session.scalars(statement))

    def list_enabled(self) -> list[SupportedDiet]:
        statement = (
            select(SupportedDiet)
            .where(SupportedDiet.is_enabled.is_(True))
            .order_by(SupportedDiet.id.asc())
        )
        return list(self._session.scalars(statement))

    def get_by_code(self, *, code: str) -> SupportedDiet | None:
        statement = select(SupportedDiet).where(SupportedDiet.code == code)
        return self._session.scalar(statement)


class UserDietPreferenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_user_and_diet(self, *, user_id: int, diet_id: int) -> UserDietPreference | None:
        statement = select(UserDietPreference).where(
            UserDietPreference.user_id == user_id,
            UserDietPreference.diet_id == diet_id,
        )
        return self._session.scalar(statement)

    def get_or_create(self, *, user_id: int, diet_id: int) -> tuple[UserDietPreference, bool]:
        preference = self.get_by_user_and_diet(user_id=user_id, diet_id=diet_id)
        if preference is not None:
            return preference, False

        preference = UserDietPreference(
            user_id=user_id,
            diet_id=diet_id,
            is_enabled=False,
        )
        self._session.add(preference)
        self._session.flush()
        return preference, True

    def set_enabled(
        self,
        *,
        user_id: int,
        diet_id: int,
        is_enabled: bool,
    ) -> UserDietPreference:
        preference, _created = self.get_or_create(user_id=user_id, diet_id=diet_id)
        preference.is_enabled = is_enabled
        self._session.flush()
        return preference

    def toggle(
        self,
        *,
        user_id: int,
        diet_id: int,
    ) -> UserDietPreference:
        preference, _created = self.get_or_create(user_id=user_id, diet_id=diet_id)
        preference.is_enabled = not preference.is_enabled
        self._session.flush()
        return preference

    def list_diets_for_user(self, *, user_id: int) -> list[SupportedDietView]:
        supported_diets = SupportedDietRepository(self._session).list_enabled()
        preferences = list(
            self._session.scalars(
                select(UserDietPreference).where(UserDietPreference.user_id == user_id)
            )
        )
        preferences_by_diet_id = {preference.diet_id: preference for preference in preferences}
        return [
            SupportedDietView(
                code=diet.code,
                name=diet.name,
                is_enabled=diet.is_enabled,
                is_selected=bool(
                    preferences_by_diet_id.get(diet.id) and preferences_by_diet_id[diet.id].is_enabled
                ),
            )
            for diet in supported_diets
        ]

    def list_enabled_for_user(self, *, user_id: int) -> list[SupportedDiet]:
        statement = (
            select(SupportedDiet)
            .join(UserDietPreference, UserDietPreference.diet_id == SupportedDiet.id)
            .where(
                SupportedDiet.is_enabled.is_(True),
                UserDietPreference.user_id == user_id,
                UserDietPreference.is_enabled.is_(True),
            )
            .order_by(SupportedDiet.id.asc())
        )
        return list(self._session.scalars(statement))


class EntryItemMetricRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert_metrics(
        self,
        *,
        entry_item_id: int,
        metric_values: list[EntryItemMetricValue],
    ) -> list[EntryItemMetric]:
        supported_metrics = SupportedMetricRepository(self._session).get_by_code_map(
            [metric_value.code for metric_value in metric_values]
        )
        if len(supported_metrics) != len({metric_value.code for metric_value in metric_values}):
            raise ValueError("Unknown supported metric code in metric_values")

        saved_metrics: list[EntryItemMetric] = []
        for metric_value in metric_values:
            supported_metric = supported_metrics[metric_value.code]
            statement = select(EntryItemMetric).where(
                EntryItemMetric.entry_item_id == entry_item_id,
                EntryItemMetric.metric_id == supported_metric.id,
            )
            existing_metric = self._session.scalar(statement)
            if existing_metric is None:
                existing_metric = EntryItemMetric(
                    entry_item_id=entry_item_id,
                    metric_id=supported_metric.id,
                    value=metric_value.value,
                    confidence=metric_value.confidence,
                )
                self._session.add(existing_metric)
            else:
                existing_metric.value = metric_value.value
                existing_metric.confidence = metric_value.confidence
            saved_metrics.append(existing_metric)

        self._session.flush()
        return saved_metrics


class DataExchangeFileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        user_id: int,
        direction: DataExchangeDirection,
        contract_type: str,
        original_filename: str,
        storage_path: str,
        sha256: str,
        row_count: int = 0,
        food_entry_count: int = 0,
        water_entry_count: int = 0,
        date_from: date | None = None,
        date_to: date | None = None,
        validation_message: str | None = None,
        processing_message: str | None = None,
        status: DataExchangeStatus = DataExchangeStatus.READY,
        processed_at: datetime | None = None,
    ) -> DataExchangeFile:
        exchange_file = DataExchangeFile(
            user_id=user_id,
            direction=direction,
            status=status,
            contract_type=contract_type,
            original_filename=original_filename,
            storage_path=storage_path,
            sha256=sha256,
            row_count=row_count,
            food_entry_count=food_entry_count,
            water_entry_count=water_entry_count,
            date_from=date_from,
            date_to=date_to,
            validation_message=validation_message,
            processing_message=processing_message,
            processed_at=processed_at,
        )
        self._session.add(exchange_file)
        self._session.flush()
        return exchange_file

    def count_for_user_and_direction(self, *, user_id: int, direction: DataExchangeDirection) -> int:
        statement = select(DataExchangeFile).where(
            DataExchangeFile.user_id == user_id,
            DataExchangeFile.direction == direction,
        )
        return len(list(self._session.scalars(statement)))

    def get_by_sha256(
        self,
        *,
        user_id: int,
        direction: DataExchangeDirection,
        sha256: str,
    ) -> DataExchangeFile | None:
        statement = select(DataExchangeFile).where(
            DataExchangeFile.user_id == user_id,
            DataExchangeFile.direction == direction,
            DataExchangeFile.sha256 == sha256,
        )
        return self._session.scalar(statement)

    def list_for_user(self, *, user_id: int) -> list[DataExchangeFile]:
        statement = (
            select(DataExchangeFile)
            .where(DataExchangeFile.user_id == user_id)
            .order_by(DataExchangeFile.created_at.desc(), DataExchangeFile.id.desc())
        )
        return list(self._session.scalars(statement))

    def get_by_id_for_user(self, *, file_id: int, user_id: int) -> DataExchangeFile | None:
        statement = select(DataExchangeFile).where(
            DataExchangeFile.id == file_id,
            DataExchangeFile.user_id == user_id,
        )
        return self._session.scalar(statement)

    def mark_processed(
        self,
        *,
        file_id: int,
        processing_message: str | None,
        processed_at: datetime,
    ) -> DataExchangeFile:
        exchange_file = self._session.get(DataExchangeFile, file_id)
        if exchange_file is None:
            raise ValueError(f"Data exchange file {file_id} was not found")
        exchange_file.status = DataExchangeStatus.PROCESSED
        exchange_file.processing_message = processing_message
        exchange_file.processed_at = processed_at
        self._session.flush()
        return exchange_file


class LLMIssueLogRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, issue: LLMIssueLogCreate) -> LLMIssueLog:
        stored_issue = LLMIssueLog(
            stage=issue.stage,
            error_code=issue.error_code,
            provider=issue.provider,
            model=issue.model,
            telegram_user_id=issue.telegram_user_id,
            username=issue.username,
            request_text=issue.request_text,
            raw_payload=issue.raw_payload,
            technical_message=issue.technical_message,
        )
        self._session.add(stored_issue)
        self._session.flush()
        return stored_issue

    def count_recent_by_stage(self, *, stage: LLMIssueStage, since: datetime) -> int:
        statement = select(LLMIssueLog).where(
            LLMIssueLog.stage == stage,
            LLMIssueLog.created_at >= since,
        )
        return len(list(self._session.scalars(statement)))

    def list_recent(self, *, limit: int, offset: int = 0) -> list[LLMIssueLog]:
        statement = (
            select(LLMIssueLog)
            .order_by(LLMIssueLog.created_at.desc(), LLMIssueLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(statement))

    def mark_error(
        self,
        *,
        file_id: int,
        processing_message: str,
    ) -> DataExchangeFile:
        exchange_file = self._session.get(DataExchangeFile, file_id)
        if exchange_file is None:
            raise ValueError(f"Data exchange file {file_id} was not found")
        exchange_file.status = DataExchangeStatus.ERROR
        exchange_file.processing_message = processing_message
        self._session.flush()
        return exchange_file

    def delete(self, exchange_file: DataExchangeFile) -> None:
        self._session.delete(exchange_file)
        self._session.flush()


class NutritionEstimatePersistenceService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._metric_repository = EntryItemMetricRepository(session)

    def save_resolved_estimates_for_entries(
        self,
        *,
        prepared_request: PreparedNutritionRequest,
        resolved_estimates: list[ResolvedNutritionEstimate],
    ) -> list[EntryItemMetric]:
        persisted_item_refs = {
            item_ref.client_item_id: (
                int(item_ref.entry_key.removeprefix("entry-")),
                int(item_ref.item_key.removeprefix("item-")),
            )
            for item_ref in prepared_request.item_refs
            if item_ref.entry_key.startswith("entry-")
            and item_ref.item_key.startswith("item-")
            and item_ref.entry_key.removeprefix("entry-").isdigit()
            and item_ref.item_key.removeprefix("item-").isdigit()
        }
        entry_ids = {entry_id for entry_id, _position in persisted_item_refs.values()}
        entry_items = list(
            self._session.scalars(
                select(EntryItem).where(EntryItem.entry_id.in_(entry_ids)) if entry_ids else select(EntryItem).where(False)
            )
        )
        entry_item_ids_by_pair = {
            (item.entry_id, item.position): item.id
            for item in entry_items
        }

        saved_metrics: list[EntryItemMetric] = []
        expected_client_item_ids = {item.client_item_id for item in prepared_request.item_refs}
        for estimate in resolved_estimates:
            if estimate.item_ref.client_item_id not in expected_client_item_ids:
                raise ValueError("Resolved nutrition estimate does not belong to prepared_request")

            entry_item_pair = persisted_item_refs.get(estimate.item_ref.client_item_id)
            if entry_item_pair is None:
                raise ValueError(
                    f"Entry item for client_item_id {estimate.item_ref.client_item_id!r} was not found"
                )

            entry_item_id = entry_item_ids_by_pair.get(entry_item_pair)
            if entry_item_id is None:
                raise ValueError(
                    f"Entry item for client_item_id {estimate.item_ref.client_item_id!r} was not found"
                )

            saved_metrics.extend(
                self._metric_repository.upsert_metrics(
                    entry_item_id=entry_item_id,
                    metric_values=[
                        EntryItemMetricValue(
                            code=metric.code,
                            value=metric.value,
                            confidence=metric.confidence.value,
                        )
                        for metric in estimate.metrics
                    ],
                )
            )

        return saved_metrics
