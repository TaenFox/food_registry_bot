from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, time, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.access import can_access_bot
from food_registry_bot.bot.message_routing import CONVERSATION, RuleBasedMessageRoutingService
from food_registry_bot.config import Settings
from food_registry_bot.conversation import ConversationService
from food_registry_bot.db.models import Entry, EntryType, LLMIssueStage, MiniAppEntrySubmission, User
from food_registry_bot.db.repositories import (
    UserDietPreferenceRepository,
    UserRepository,
    UserSummaryPreferenceRepository,
    insert_if_absent,
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


class MiniAppEntryError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def resolve_occurred_at(command: CreateMiniAppEntry, timezone_name: str) -> datetime:
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


class MiniAppEntryWriter:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        extraction_service: JournalExtractionService,
        nutrition_service: NutritionEstimationService,
        conversation_service: ConversationService,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.extraction_service = extraction_service
        self.nutrition_service = nutrition_service
        self.conversation_service = conversation_service

    @staticmethod
    def _user(session: Session, telegram_user_id: int, admin_user_ids: tuple[int, ...]) -> User:
        if not can_access_bot(session, telegram_user_id, admin_user_ids):
            raise MiniAppEntryError(403, "access_denied")
        user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
        if user is None:
            raise MiniAppEntryError(404, "profile_missing")
        return user

    @staticmethod
    def _food_day(session: Session, user: User, occurred_at: datetime) -> tuple[date, int]:
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

    def write(
        self, telegram_user_id: int, admin_user_ids: tuple[int, ...], command: CreateMiniAppEntry,
    ) -> date:
        request_id = command.request_id.hex
        fingerprint = hashlib.sha256(
            command.model_dump_json(exclude={"request_id"}).encode()
        ).hexdigest()

        with self.session_factory.begin() as session:
            user = self._user(session, telegram_user_id, admin_user_ids)
            occurred_at = resolve_occurred_at(command, user.timezone)
            summary_date, _ = self._food_day(session, user, occurred_at)
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
                raise MiniAppEntryError(503, "entry_provider_unavailable")

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
                user = self._user(session, telegram_user_id, admin_user_ids)
                occurred_at = resolve_occurred_at(command, user.timezone)
                summary_date, start_hour = self._food_day(session, user, occurred_at)
                claimed = insert_if_absent(
                    session, MiniAppEntrySubmission,
                    {"user_id": user.id, "request_id": request_id, "fingerprint": fingerprint},
                    ("user_id", "request_id"),
                )
                if claimed is None:
                    existing = session.scalars(select(MiniAppEntrySubmission).filter_by(
                        user_id=user.id, request_id=request_id,
                    )).one()
                    if existing.fingerprint != fingerprint:
                        raise MiniAppEntryError(409, "entry_request_conflict")
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
                        raise MiniAppEntryError(503, "entry_provider_unavailable")
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
