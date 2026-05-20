from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from food_registry_bot.db.models import (
    Entry,
    EntryItem,
    EntryItemMetric,
    EntryType,
    MealType,
    SupportedMetric,
    DailyGoalSnapshot,
    ConversationMessage,
    ConversationMessageRole,
    ConversationSession,
    User,
    UserAccess,
    UserGoalPreference,
    UserSummaryPreference,
)
if TYPE_CHECKING:
    from food_registry_bot.nutrition.journal_adapter import PreparedNutritionRequest, ResolvedNutritionEstimate

SUPPORTED_NUTRITION_DAY_START_HOURS = (0, 2, 4, 6)
SUPPORTED_SUMMARY_DISPLAY_MODES = ("text", "bars")
SUPPORTED_GOAL_METRIC_CODES = ("calories", "protein", "fat", "carbs", "fiber", "water")
DEFAULT_DAILY_GOALS = {
    "calories": 1800,
    "protein": 90,
    "fat": 60,
    "carbs": 210,
    "fiber": 25,
    "water": 2000,
}


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
class KnownUserAccessView:
    telegram_user_id: int
    username: str | None
    has_profile: bool
    is_allowed: bool


@dataclass(frozen=True)
class ConversationTurn:
    role: str
    content: str
    created_at: datetime


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
    ) -> User:
        user = User(
            telegram_user_id=telegram_user_id,
            username=username,
            timezone=timezone,
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
    ) -> tuple[User, bool]:
        user = self.get_by_telegram_user_id(telegram_user_id)
        if user is not None:
            return user, False

        user = self.create(
            telegram_user_id=telegram_user_id,
            username=username,
            timezone=timezone,
        )
        return user, True


class UserAccessRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_telegram_user_id(self, telegram_user_id: int) -> Optional[UserAccess]:
        statement = select(UserAccess).where(UserAccess.telegram_user_id == telegram_user_id)
        return self._session.scalar(statement)

    def is_allowed(self, telegram_user_id: int) -> bool:
        access = self.get_by_telegram_user_id(telegram_user_id)
        return bool(access and access.is_allowed)

    def set_access(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        is_allowed: bool,
    ) -> UserAccess:
        access = self.get_by_telegram_user_id(telegram_user_id)
        if access is None:
            access = UserAccess(
                telegram_user_id=telegram_user_id,
                username=username,
                is_allowed=is_allowed,
            )
            self._session.add(access)
        else:
            access.username = username
            access.is_allowed = is_allowed

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
                )
            )

        return result


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
            show_post_entry_delta_suffix=True,
            summary_display_mode="text",
            nutrition_day_start_hour=4,
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

    def list_recent_for_user(self, *, user_id: int, limit: int = 5) -> list[Entry]:
        statement = (
            select(Entry)
            .where(Entry.user_id == user_id)
            .options(selectinload(Entry.items))
            .order_by(Entry.occurred_at.desc(), Entry.id.desc())
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
            .options(selectinload(Entry.items))
        )
        return self._session.scalar(statement)

    def delete(self, entry: Entry) -> None:
        self._session.delete(entry)
        self._session.flush()

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
            .options(selectinload(Entry.items))
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
                if item_metric_codes != required_metric_code_set:
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
                if item_metric_codes != required_metric_code_set:
                    count += 1
                    break

        return count


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
