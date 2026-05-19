from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from food_registry_bot.db.models import Entry, EntryType
from food_registry_bot.extraction.contract import ExtractedJournalPayload
from food_registry_bot.nutrition.contract import (
    NutritionConfidence,
    NutritionEstimationItemInput,
    NutritionEstimationPayload,
    NutritionEstimationRequest,
    NutritionUnit,
)


@dataclass(frozen=True)
class NutritionJournalItemRef:
    client_item_id: str
    entry_key: str
    item_key: str
    name: str
    quantity: int | None
    unit: str | None


@dataclass(frozen=True)
class PreparedNutritionRequest:
    request: NutritionEstimationRequest
    item_refs: list[NutritionJournalItemRef]


@dataclass(frozen=True)
class ResolvedNutritionMetric:
    code: str
    value: float
    confidence: NutritionConfidence


@dataclass(frozen=True)
class ResolvedNutritionEstimate:
    item_ref: NutritionJournalItemRef
    metrics: list[ResolvedNutritionMetric]


def _is_supported_food_item(*, entry_type: EntryType) -> bool:
    return entry_type is EntryType.FOOD


def _sanitize_quantity_unit(
    *,
    quantity: int | None,
    unit: str | None,
) -> tuple[int | None, str | None]:
    if quantity is None or unit is None:
        return None, None

    normalized_unit = unit.strip().lower()
    unit_aliases = {
        "g": NutritionUnit.GRAM.value,
        "гр": NutritionUnit.GRAM.value,
        "г": NutritionUnit.GRAM.value,
        "ml": NutritionUnit.MILLILITER.value,
        "мл": NutritionUnit.MILLILITER.value,
    }
    canonical_unit = unit_aliases.get(normalized_unit)
    if canonical_unit is None:
        return None, None

    return quantity, canonical_unit


def _build_prepared_request(item_refs: Iterable[NutritionJournalItemRef]) -> PreparedNutritionRequest | None:
    collected_refs = list(item_refs)
    if not collected_refs:
        return None

    return PreparedNutritionRequest(
        request=NutritionEstimationRequest(
            items=[
                NutritionEstimationItemInput(
                    client_item_id=item_ref.client_item_id,
                    name=item_ref.name,
                    quantity=item_ref.quantity,
                    unit=item_ref.unit,
                )
                for item_ref in collected_refs
            ]
        ),
        item_refs=collected_refs,
    )


def prepare_nutrition_request_from_extracted_payload(
    payload: ExtractedJournalPayload,
) -> PreparedNutritionRequest | None:
    item_refs: list[NutritionJournalItemRef] = []

    for entry_index, entry in enumerate(payload.entries):
        if not _is_supported_food_item(entry_type=entry.type):
            continue

        for item_index, item in enumerate(entry.items):
            quantity, unit = _sanitize_quantity_unit(quantity=item.quantity, unit=item.unit)
            item_refs.append(
                NutritionJournalItemRef(
                    client_item_id=f"entry-{entry_index}:item-{item_index}",
                    entry_key=f"entry-{entry_index}",
                    item_key=f"item-{item_index}",
                    name=item.name,
                    quantity=quantity,
                    unit=unit,
                )
            )

    return _build_prepared_request(item_refs)


def prepare_nutrition_request_from_entries(entries: Iterable[Entry]) -> PreparedNutritionRequest | None:
    item_refs: list[NutritionJournalItemRef] = []

    for entry in entries:
        if not _is_supported_food_item(entry_type=entry.entry_type):
            continue

        for item in sorted(entry.items, key=lambda current: current.position):
            quantity, unit = _sanitize_quantity_unit(quantity=item.quantity, unit=item.unit)
            item_refs.append(
                NutritionJournalItemRef(
                    client_item_id=f"entry-{entry.id}:item-{item.position}",
                    entry_key=f"entry-{entry.id}",
                    item_key=f"item-{item.position}",
                    name=item.name,
                    quantity=quantity,
                    unit=unit,
                )
            )

    return _build_prepared_request(item_refs)


def resolve_nutrition_estimates(
    prepared_request: PreparedNutritionRequest,
    payload: NutritionEstimationPayload,
) -> list[ResolvedNutritionEstimate]:
    items_by_client_id = {item.client_item_id: item for item in payload.items}

    return [
        ResolvedNutritionEstimate(
            item_ref=item_ref,
            metrics=[
                ResolvedNutritionMetric(
                    code=metric.code,
                    value=metric.value,
                    confidence=metric.confidence,
                )
                for metric in items_by_client_id[item_ref.client_item_id].metrics
            ],
        )
        for item_ref in prepared_request.item_refs
    ]
