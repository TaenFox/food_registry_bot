from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, time, timezone
from threading import Condition
from typing import Literal, Optional, Union
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.access import can_access_bot
from food_registry_bot.bot.message_routing import CONVERSATION, RuleBasedMessageRoutingService
from food_registry_bot.config import Settings
from food_registry_bot.conversation import ConversationService
from food_registry_bot.db.models import Entry, EntryItem, EntryType, LLMIssueStage, MiniAppEntrySubmission, User
from food_registry_bot.db.repositories import (
    EntryItemCreate,
    EntryRepository,
    UserDietPreferenceRepository,
    UserRepository,
    UserSummaryPreferenceRepository,
    insert_if_absent,
)
from food_registry_bot.diary_editing import (
    DiaryEditError,
    PositiveInteger,
    VersionedDishAction,
    dish_metric_values,
    dish_revision,
    lock_diary_entries,
)
from food_registry_bot.diet import DisabledDietEvaluationService
from food_registry_bot.extraction import InvalidExtractionPayload, JournalExtractionRequest
from food_registry_bot.extraction.service import JournalExtractionService
from food_registry_bot.journal_write import (
    build_diet_evaluation_request_for_supported_diets,
    build_fixed_diet_payload_for_water_entries,
    log_llm_issue,
    persist_diet_scores,
    persist_extracted_entry,
    resolve_diet_service,
)
from food_registry_bot.llm_access.resolver import UserLLMRuntimeBundle, build_user_llm_runtime_bundle
from food_registry_bot.llm_access.trial import TrialQuota, charge_trial_request, trial_has_expired, trial_limit_message
from food_registry_bot.meal_repeat import copy_food_items
from food_registry_bot.nutrition.beverage import is_plain_water
from food_registry_bot.nutrition.contract import NutritionEstimationItemInput
from food_registry_bot.nutrition.daily_summary import resolve_local_summary_date
from food_registry_bot.nutrition.goals import DailyNutritionGoalSnapshotUseCase
from food_registry_bot.nutrition.service import InvalidNutritionPayload, NutritionEstimationService
from food_registry_bot.nutrition.use_cases import (
    FailedNutritionEstimation,
    StoredEntryNutritionEstimationUseCase,
    SuccessfulNutritionEstimation,
)

logger = logging.getLogger(__name__)


class CreateMiniAppEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    request_id: UUID
    text: str = Field(min_length=1, max_length=800)
    local_date: date
    local_time: time


class RepeatDish(VersionedDishAction):
    quantity: Optional[PositiveInteger] = None


class RepeatMiniAppEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: UUID
    local_date: date
    local_time: time
    items: list[RepeatDish] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def unique_items(self) -> RepeatMiniAppEntry:
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("Each source dish can appear only once")
        return self


class QuickWaterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["water_250"]
    request_id: UUID
    local_date: date
    local_time: time


MINI_APP_ENTRY_COMMAND = TypeAdapter(Union[CreateMiniAppEntry, RepeatMiniAppEntry, QuickWaterEntry])


class MiniAppEntryError(Exception):
    def __init__(self, status: int, code: str, message: str | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


def resolve_occurred_at(command: CreateMiniAppEntry | RepeatMiniAppEntry | QuickWaterEntry, timezone_name: str) -> datetime:
    if command.local_time.tzinfo is not None:
        raise MiniAppEntryError(422, "invalid_entry_time")
    local = datetime.combine(command.local_date, command.local_time)
    user_timezone = ZoneInfo(timezone_name)
    try:
        utc = local.replace(tzinfo=user_timezone).astimezone(timezone.utc)
        if utc.astimezone(user_timezone).replace(tzinfo=None) == local:
            return utc
    except (OverflowError, ValueError):
        pass
    raise MiniAppEntryError(422, "invalid_entry_time")


def entry_user(session: Session, telegram_user_id: int, admin_user_ids: tuple[int, ...]) -> User:
    if not can_access_bot(session, telegram_user_id, admin_user_ids):
        raise MiniAppEntryError(403, "access_denied")
    user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
    if user is None:
        raise MiniAppEntryError(404, "profile_missing")
    return user


def entry_food_day(session: Session, user: User, occurred_at: datetime) -> tuple[date, int]:
    preference = UserSummaryPreferenceRepository(session).get_by_user_id(user.id)
    start_hour = preference.nutrition_day_start_hour if preference else 4
    try:
        summary_date = resolve_local_summary_date(
            reference_at=occurred_at, timezone_name=user.timezone,
            nutrition_day_start_hour=start_hour,
        )
    except OverflowError as exc:
        raise MiniAppEntryError(422, "invalid_entry_time") from exc
    return summary_date, start_hour


def claim_entry_submission(session: Session, user_id: int, request_id: str, fingerprint: str) -> bool:
    claimed = insert_if_absent(
        session, MiniAppEntrySubmission,
        {"user_id": user_id, "request_id": request_id, "fingerprint": fingerprint},
        ("user_id", "request_id"),
    )
    if claimed is not None:
        return True
    existing = session.scalars(select(MiniAppEntrySubmission).filter_by(
        user_id=user_id, request_id=request_id,
    )).one()
    if existing.fingerprint != fingerprint:
        raise MiniAppEntryError(409, "entry_request_conflict")
    return False


def _repeat_entry_items(
    session: Session, user: User, occurred_at: datetime, command: RepeatMiniAppEntry,
) -> None:
    try:
        entries = lock_diary_entries(
            session, user.id, {item.entry_id for item in command.items}, lock_metrics=True,
        )
    except DiaryEditError as exc:
        raise MiniAppEntryError(exc.status, exc.code) from exc
    sources = {item.id: item for entry in entries.values() for item in entry.items}
    portions: list[tuple[EntryItem, int | None]] = []
    for requested in command.items:
        item = sources.get(requested.item_id)
        if (item is None or item.entry_id != requested.entry_id
                or dish_revision(item, dish_metric_values(item)) != requested.revision):
            raise MiniAppEntryError(409, "dish_changed")
        if requested.quantity is not None and requested.quantity != item.quantity:
            if (not item.quantity or item.quantity < 0
                    or NutritionEstimationItemInput.normalize_unit(item.unit) not in ("g", "ml")):
                raise MiniAppEntryError(422, "portion_not_scalable")
        portions.append((item, requested.quantity))
    entry_type = EntryType.WATER if all(is_plain_water(item) for item, _ in portions) else EntryType.FOOD
    copy_food_items(
        session, user_id=user.id, occurred_at=occurred_at, portions=portions, entry_type=entry_type,
    )


def write_prepared_entry(
    session_factory: sessionmaker[Session], telegram_user_id: int, admin_user_ids: tuple[int, ...],
    command: RepeatMiniAppEntry | QuickWaterEntry,
) -> date:
    request_id = command.request_id.hex
    fingerprint = hashlib.sha256(command.model_dump_json(exclude={"request_id"}).encode()).hexdigest()
    with session_factory.begin() as session:
        user = entry_user(session, telegram_user_id, admin_user_ids)
        occurred_at = resolve_occurred_at(command, user.timezone)
        summary_date, start_hour = entry_food_day(session, user, occurred_at)
        if not claim_entry_submission(session, user.id, request_id, fingerprint):
            return summary_date

        if isinstance(command, QuickWaterEntry):
            entry = EntryRepository(session).create(
                user_id=user.id, occurred_at=occurred_at, entry_type=EntryType.WATER,
                items=[EntryItemCreate(name="water", quantity=250, unit="ml")],
            )
            diets = UserDietPreferenceRepository(session).list_enabled_for_user(user_id=user.id)
            fixed = build_fixed_diet_payload_for_water_entries([entry], diets)
            if fixed is not None:
                persist_diet_scores(session=session, entries=[entry], evaluation_payload=fixed)
        else:
            _repeat_entry_items(session, user, occurred_at, command)
        DailyNutritionGoalSnapshotUseCase(session).get_or_create(
            user_id=user.id, summary_date=summary_date, timezone_name=user.timezone,
            nutrition_day_start_hour=start_hour,
        )
        return summary_date


class MiniAppEntryWriter:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        extraction_service: JournalExtractionService,
        nutrition_service: NutritionEstimationService,
        conversation_service: ConversationService,
        *,
        trial_quota: TrialQuota | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.extraction_service = extraction_service
        self.nutrition_service = nutrition_service
        self.conversation_service = conversation_service
        self.trial_quota = trial_quota
        self._in_flight: set[tuple[int, UUID]] = set()
        self._in_flight_condition = Condition()

    def write(
        self, telegram_user_id: int, admin_user_ids: tuple[int, ...], command: CreateMiniAppEntry,
    ) -> date:
        key = telegram_user_id, command.request_id
        with self._in_flight_condition:
            self._in_flight_condition.wait_for(lambda: key not in self._in_flight)
            self._in_flight.add(key)
        try:
            return self._write(telegram_user_id, admin_user_ids, command)
        finally:
            with self._in_flight_condition:
                self._in_flight.remove(key)
                self._in_flight_condition.notify_all()

    def _write(
        self, telegram_user_id: int, admin_user_ids: tuple[int, ...], command: CreateMiniAppEntry,
    ) -> date:
        request_id = command.request_id.hex
        fingerprint = hashlib.sha256(
            command.model_dump_json(exclude={"request_id"}).encode()
        ).hexdigest()

        with self.session_factory.begin() as session:
            user = entry_user(session, telegram_user_id, admin_user_ids)
            occurred_at = resolve_occurred_at(command, user.timezone)
            summary_date, _ = entry_food_day(session, user, occurred_at)
            existing = session.get(MiniAppEntrySubmission, (user.id, request_id))
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise MiniAppEntryError(409, "entry_request_conflict")
                return summary_date

            request = JournalExtractionRequest(text=command.text)
            decision = RuleBasedMessageRoutingService().route(request)
            if decision.route == CONVERSATION or command.text.startswith(("/", "{")):
                raise MiniAppEntryError(422, "not_a_food_record")
            runtime = build_user_llm_runtime_bundle(
                session=session, settings=self.settings, user_id=user.id,
                telegram_user_id=telegram_user_id, admin_user_ids=admin_user_ids,
                fallback_extraction_service=self.extraction_service,
                fallback_nutrition_service=self.nutrition_service,
                fallback_conversation_service=self.conversation_service,
            )
            username = user.username
            if runtime.extraction_access is not None and not runtime.extraction_access.is_available:
                if runtime.extraction_access.source == "personal" and trial_has_expired(session, telegram_user_id):
                    raise MiniAppEntryError(422, "trial_expired")
                raise MiniAppEntryError(503, "entry_provider_unavailable")
            resets_at = charge_trial_request(
                self.trial_quota, session, user, admin_user_ids, "food", runtime.extraction_access,
            )
            if resets_at is not None:
                raise MiniAppEntryError(429, "trial_limit_exceeded", trial_limit_message("food", resets_at, user.timezone))

        result = runtime.extraction_service.extract(request)
        if isinstance(result, InvalidExtractionPayload):
            self._log_issue(result, LLMIssueStage.EXTRACTION, telegram_user_id, username, command.text)
            raise MiniAppEntryError(422, "entry_not_recognized")
        if result is None:
            raise MiniAppEntryError(422, "entry_not_recognized")
        if any(entry.type not in (EntryType.FOOD, EntryType.WATER) for entry in result.payload.entries):
            raise MiniAppEntryError(422, "not_a_food_record")

        nutrition_issue = None
        try:
            with self.session_factory.begin() as session:
                user = entry_user(session, telegram_user_id, admin_user_ids)
                occurred_at = resolve_occurred_at(command, user.timezone)
                summary_date, start_hour = entry_food_day(session, user, occurred_at)
                if not claim_entry_submission(session, user.id, request_id, fingerprint):
                    return summary_date

                entries = [
                    persist_extracted_entry(
                        session=session,
                        user_id=user.id,
                        extracted_entry=entry.model_copy(update={"occurred_at": occurred_at}),
                        extraction_result=result,
                        occurred_at=occurred_at,
                        source_text=command.text,
                    )
                    for entry in result.payload.entries
                ]
                food_ids = [entry.id for entry in entries if entry.entry_type is EntryType.FOOD]
                if food_ids:
                    if runtime.nutrition_access is not None and not runtime.nutrition_access.is_available:
                        if runtime.nutrition_access.source == "personal" and trial_has_expired(session, telegram_user_id):
                            raise MiniAppEntryError(422, "trial_expired")
                        raise MiniAppEntryError(503, "entry_provider_unavailable")
                    if runtime.extraction_access is None:
                        resets_at = charge_trial_request(
                            self.trial_quota, session, user, admin_user_ids, "food", runtime.nutrition_access,
                        )
                        if resets_at is not None:
                            raise MiniAppEntryError(
                                429, "trial_limit_exceeded", trial_limit_message("food", resets_at, user.timezone),
                            )
                    nutrition = StoredEntryNutritionEstimationUseCase(
                        session, runtime.nutrition_service
                    ).run(entry_ids=food_ids)
                    if not isinstance(nutrition, SuccessfulNutritionEstimation):
                        if isinstance(nutrition, FailedNutritionEstimation):
                            nutrition_issue = nutrition.issue
                        raise MiniAppEntryError(422, "entry_nutrition_failed")

                self._save_diet_scores(session, user, entries, runtime)
                DailyNutritionGoalSnapshotUseCase(session).get_or_create(
                    user_id=user.id, summary_date=summary_date, timezone_name=user.timezone,
                    nutrition_day_start_hour=start_hour,
                )
                return summary_date
        except MiniAppEntryError:
            if nutrition_issue is not None:
                self._log_issue(
                    nutrition_issue, LLMIssueStage.NUTRITION, telegram_user_id, username, command.text
                )
            raise

    def _save_diet_scores(
        self, session: Session, user: User, entries: list[Entry], runtime: UserLLMRuntimeBundle,
    ) -> None:
        diets = UserDietPreferenceRepository(session).list_enabled_for_user(user_id=user.id)
        fixed = build_fixed_diet_payload_for_water_entries(entries, diets)
        if fixed is not None:
            persist_diet_scores(session=session, entries=entries, evaluation_payload=fixed)
        request = build_diet_evaluation_request_for_supported_diets(entries, diets)
        if request is None:
            return
        service = resolve_diet_service(
            settings=self.settings, runtime_bundle=runtime,
            fallback_diet_service=DisabledDietEvaluationService(),
        )
        try:
            result = service.evaluate(request)
        except Exception:
            logger.exception("Diet evaluation failed for a Mini App entry")
            return
        if hasattr(result, "payload"):
            persist_diet_scores(session=session, entries=entries, evaluation_payload=result.payload)

    def _log_issue(
        self, issue: InvalidExtractionPayload | InvalidNutritionPayload, stage: LLMIssueStage,
        telegram_user_id: int, username: str | None, text: str,
    ) -> None:
        if issue.is_llm:
            log_llm_issue(
                session_factory=self.session_factory, stage=stage,
                error_code=issue.error_code, provider=issue.provider, model=issue.model,
                telegram_user_id=telegram_user_id, username=username, request_text=text,
                raw_payload=issue.raw_payload, technical_message=issue.technical_message,
            )
