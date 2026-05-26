from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from food_registry_bot.db.repositories import EntryRepository, NutritionEstimatePersistenceService
from food_registry_bot.nutrition.contract import SUPPORTED_NUTRITION_METRIC_CODES
from food_registry_bot.nutrition.journal_adapter import (
    prepare_nutrition_request_from_entries,
    resolve_nutrition_estimates,
)
from food_registry_bot.nutrition.service import InvalidNutritionPayload, NutritionEstimationService


@dataclass(frozen=True)
class SuccessfulNutritionEstimation:
    entry_ids: list[int]
    estimated_item_count: int
    saved_metric_count: int
    metric_totals: dict[str, float]


@dataclass(frozen=True)
class SkippedNutritionEstimation:
    reason: str


@dataclass(frozen=True)
class FailedNutritionEstimation:
    message: str
    issue: InvalidNutritionPayload | None = None


@dataclass(frozen=True)
class NutritionBackfillEntryFailure:
    entry_id: int
    message: str


@dataclass(frozen=True)
class NutritionBackfillCompleted:
    selected_entry_ids: list[int]
    processed_entry_ids: list[int]
    skipped_entry_ids: list[int]
    failed_entries: list[NutritionBackfillEntryFailure]
    saved_metric_count: int


@dataclass(frozen=True)
class NutritionBackfillProgress:
    selected_entry_count: int
    processed_entry_count: int
    skipped_entry_count: int
    failed_entry_count: int
    current_entry_id: int | None


class StoredEntryNutritionEstimationUseCase:
    def __init__(
        self,
        session: Session,
        nutrition_service: NutritionEstimationService,
    ) -> None:
        self._session = session
        self._entry_repository = EntryRepository(session)
        self._persistence_service = NutritionEstimatePersistenceService(session)
        self._nutrition_service = nutrition_service

    def run(
        self,
        *,
        entry_ids: list[int],
    ) -> SuccessfulNutritionEstimation | SkippedNutritionEstimation | FailedNutritionEstimation:
        entries = self._entry_repository.list_by_ids(entry_ids=entry_ids)
        if not entries:
            return SkippedNutritionEstimation(reason="No entries found for nutrition estimation.")

        prepared_request = prepare_nutrition_request_from_entries(entries)
        if prepared_request is None:
            return SkippedNutritionEstimation(
                reason="No supported food items with quantity and unit found for nutrition estimation."
            )

        nutrition_result = self._nutrition_service.estimate(prepared_request.request)
        if isinstance(nutrition_result, InvalidNutritionPayload):
            return FailedNutritionEstimation(message=nutrition_result.message, issue=nutrition_result)

        resolved_estimates = resolve_nutrition_estimates(prepared_request, nutrition_result.payload)
        saved_metrics = self._persistence_service.save_resolved_estimates_for_entries(
            prepared_request=prepared_request,
            resolved_estimates=resolved_estimates,
        )
        metric_totals: dict[str, float] = {}
        for estimate in resolved_estimates:
            for metric in estimate.metrics:
                metric_totals[metric.code] = metric_totals.get(metric.code, 0.0) + metric.value

        return SuccessfulNutritionEstimation(
            entry_ids=[entry.id for entry in entries],
            estimated_item_count=len(resolved_estimates),
            saved_metric_count=len(saved_metrics),
            metric_totals=metric_totals,
        )


class BackfillNutritionEstimationUseCase:
    def __init__(
        self,
        session: Session,
        nutrition_service: NutritionEstimationService,
    ) -> None:
        self._session = session
        self._entry_repository = EntryRepository(session)
        self._nutrition_service = nutrition_service

    def run(
        self,
        *,
        limit: int = 20,
        progress_callback: Callable[[NutritionBackfillProgress], None] | None = None,
    ) -> NutritionBackfillCompleted:
        selected_entry_ids = self._entry_repository.list_incomplete_food_entry_ids(
            required_metric_codes=list(SUPPORTED_NUTRITION_METRIC_CODES),
            limit=limit,
        )

        processed_entry_ids: list[int] = []
        skipped_entry_ids: list[int] = []
        failed_entries: list[NutritionBackfillEntryFailure] = []
        saved_metric_count = 0
        single_entry_use_case = StoredEntryNutritionEstimationUseCase(
            self._session,
            self._nutrition_service,
        )
        if progress_callback is not None:
            progress_callback(
                NutritionBackfillProgress(
                    selected_entry_count=len(selected_entry_ids),
                    processed_entry_count=0,
                    skipped_entry_count=0,
                    failed_entry_count=0,
                    current_entry_id=None,
                )
            )

        for entry_id in selected_entry_ids:
            result = single_entry_use_case.run(entry_ids=[entry_id])
            if isinstance(result, SuccessfulNutritionEstimation):
                processed_entry_ids.append(entry_id)
                saved_metric_count += result.saved_metric_count
            elif isinstance(result, SkippedNutritionEstimation):
                skipped_entry_ids.append(entry_id)
            else:
                failed_entries.append(
                    NutritionBackfillEntryFailure(
                        entry_id=entry_id,
                        message=result.message,
                    )
                )
            if progress_callback is not None:
                progress_callback(
                    NutritionBackfillProgress(
                        selected_entry_count=len(selected_entry_ids),
                        processed_entry_count=len(processed_entry_ids),
                        skipped_entry_count=len(skipped_entry_ids),
                        failed_entry_count=len(failed_entries),
                        current_entry_id=entry_id,
                    )
                )

        return NutritionBackfillCompleted(
            selected_entry_ids=selected_entry_ids,
            processed_entry_ids=processed_entry_ids,
            skipped_entry_ids=skipped_entry_ids,
            failed_entries=failed_entries,
            saved_metric_count=saved_metric_count,
        )
