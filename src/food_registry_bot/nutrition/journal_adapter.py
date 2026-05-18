from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from food_registry_bot.db.models import Entry, EntryType
from food_registry_bot.extraction.contract import ExtractedJournalPayload
from food_registry_bot.nutrition.contract import (
    NutritionEstimationItemInput,
    NutritionEstimationPayload,
    NutritionEstimationRequest,
)


@dataclass(frozen=True)
class NutritionJournalItemRef:
    client_item_id: str
    entry_key: str
    item_key: str
    name: str
    quantity: int
    unit: str


@dataclass(frozen=True)
class PreparedNutritionRequest:
    request: NutritionEstimationRequest
    item_refs: list[NutritionJournalItemRef]


@dataclass(frozen=True)
class ResolvedNutritionEstimate:
    item_ref: NutritionJournalItemRef
    calories: int
    protein: float
    fat: float
    carbs: float


def _is_supported_food_item(*, entry_type: EntryType, quantity: int | None, unit: str | None) -> bool:
    if entry_type is not EntryType.FOOD:
        return False
    if quantity is None or unit is None:
        return False
    return unit in {"g", "ml"}


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
        for item_index, item in enumerate(entry.items):
            if not _is_supported_food_item(
                entry_type=entry.type,
                quantity=item.quantity,
                unit=item.unit,
            ):
                continue

            item_refs.append(
                NutritionJournalItemRef(
                    client_item_id=f"entry-{entry_index}:item-{item_index}",
                    entry_key=f"entry-{entry_index}",
                    item_key=f"item-{item_index}",
                    name=item.name,
                    quantity=item.quantity,
                    unit=item.unit,
                )
            )

    return _build_prepared_request(item_refs)


def prepare_nutrition_request_from_entries(entries: Iterable[Entry]) -> PreparedNutritionRequest | None:
    item_refs: list[NutritionJournalItemRef] = []

    for entry in entries:
        for item in sorted(entry.items, key=lambda current: current.position):
            if not _is_supported_food_item(
                entry_type=entry.entry_type,
                quantity=item.quantity,
                unit=item.unit,
            ):
                continue

            item_refs.append(
                NutritionJournalItemRef(
                    client_item_id=f"entry-{entry.id}:item-{item.position}",
                    entry_key=f"entry-{entry.id}",
                    item_key=f"item-{item.position}",
                    name=item.name,
                    quantity=item.quantity,
                    unit=item.unit,
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
            calories=items_by_client_id[item_ref.client_item_id].calories,
            protein=items_by_client_id[item_ref.client_item_id].protein,
            fat=items_by_client_id[item_ref.client_item_id].fat,
            carbs=items_by_client_id[item_ref.client_item_id].carbs,
        )
        for item_ref in prepared_request.item_refs
    ]
