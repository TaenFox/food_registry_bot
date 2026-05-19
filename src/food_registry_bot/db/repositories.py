from __future__ import annotations

from datetime import datetime
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
    User,
    UserAccess,
    UserSummaryPreference,
)
if TYPE_CHECKING:
    from food_registry_bot.nutrition.journal_adapter import PreparedNutritionRequest, ResolvedNutritionEstimate


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

        preference = UserSummaryPreference(user_id=user_id, show_calories=True)
        self._session.add(preference)
        self._session.flush()
        return preference, True

    def set_show_calories(
        self,
        *,
        user_id: int,
        show_calories: bool,
    ) -> UserSummaryPreference:
        preference, _created = self.get_or_create(user_id=user_id)
        preference.show_calories = show_calories
        self._session.flush()
        return preference

    def toggle_show_calories(self, *, user_id: int) -> UserSummaryPreference:
        preference, _created = self.get_or_create(user_id=user_id)
        preference.show_calories = not preference.show_calories
        self._session.flush()
        return preference


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
