from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from food_registry_bot.db.repositories import EntryRepository, NutritionEstimatePersistenceService
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


@dataclass(frozen=True)
class SkippedNutritionEstimation:
    reason: str


@dataclass(frozen=True)
class FailedNutritionEstimation:
    message: str


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
            return FailedNutritionEstimation(message=nutrition_result.message)

        resolved_estimates = resolve_nutrition_estimates(prepared_request, nutrition_result.payload)
        saved_metrics = self._persistence_service.save_resolved_estimates_for_entries(
            prepared_request=prepared_request,
            resolved_estimates=resolved_estimates,
        )

        return SuccessfulNutritionEstimation(
            entry_ids=[entry.id for entry in entries],
            estimated_item_count=len(resolved_estimates),
            saved_metric_count=len(saved_metrics),
        )
