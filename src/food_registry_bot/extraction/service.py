from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from food_registry_bot.extraction.contract import ExtractedJournalPayload
from food_registry_bot.extraction.llm_client import LLMExtractionClient, LLMExtractionClientError
from food_registry_bot.extraction.request import JournalExtractionRequest


@dataclass(frozen=True)
class ValidExtractionPayload:
    payload: ExtractedJournalPayload
    extraction_provider: str
    extraction_model: str | None
    raw_payload: str


@dataclass(frozen=True)
class InvalidExtractionPayload:
    message: str
    provider: str | None = None
    model: str | None = None
    raw_payload: str | None = None
    technical_message: str | None = None
    error_code: str | None = None
    is_llm: bool = False


class JournalExtractionService(Protocol):
    def extract(
        self, request: JournalExtractionRequest
    ) -> ValidExtractionPayload | InvalidExtractionPayload | None:
        """Extract a structured journal payload or return None when extraction is not attempted."""


class StructuredPayloadExtractionService:
    def extract(
        self, request: JournalExtractionRequest
    ) -> ValidExtractionPayload | InvalidExtractionPayload | None:
        if request.images:
            return None

        if request.text is None:
            return None

        stripped_text = request.text.strip()
        if not stripped_text.startswith("{"):
            return None

        try:
            payload = ExtractedJournalPayload.model_validate_json(stripped_text)
        except (ValidationError, json.JSONDecodeError) as exc:
            return InvalidExtractionPayload(
                message=(
                    "Не удалось разобрать structured payload. "
                    "Ожидаю объект вида {'entries': [...]} с type, items и name."
                ),
                provider="structured_payload",
                raw_payload=stripped_text,
                technical_message=str(exc),
                error_code="invalid_payload",
            )

        return ValidExtractionPayload(
            payload=payload,
            extraction_provider="structured_payload",
            extraction_model=None,
            raw_payload=stripped_text,
        )


class LLMExtractionService:
    def __init__(self, client: LLMExtractionClient) -> None:
        self._client = client

    def extract(
        self, request: JournalExtractionRequest
    ) -> ValidExtractionPayload | InvalidExtractionPayload | None:
        try:
            raw_payload = self._client.extract_journal_payload(request)
        except LLMExtractionClientError as exc:
            return InvalidExtractionPayload(
                message="Не удалось получить structured payload от LLM.",
                provider=self._client.provider_name,
                model=self._client.model_name,
                technical_message=str(exc),
                error_code="client_error",
                is_llm=True,
            )

        if not raw_payload.strip():
            return InvalidExtractionPayload(
                message="LLM вернула пустой structured payload для записи журнала.",
                provider=self._client.provider_name,
                model=self._client.model_name,
                raw_payload=raw_payload,
                technical_message="OpenAI returned an empty extraction response",
                error_code="empty_payload",
                is_llm=True,
            )

        normalized_raw_payload = _normalize_llm_raw_payload(raw_payload)

        try:
            payload = ExtractedJournalPayload.model_validate_json(normalized_raw_payload)
        except (ValidationError, json.JSONDecodeError) as exc:
            return InvalidExtractionPayload(
                message=(
                    "LLM вернула невалидный structured payload. "
                    "Ожидаю объект вида {'entries': [...]} с type, items и name."
                ),
                provider=self._client.provider_name,
                model=self._client.model_name,
                raw_payload=normalized_raw_payload,
                technical_message=str(exc),
                error_code="invalid_payload",
                is_llm=True,
            )

        return ValidExtractionPayload(
            payload=payload,
            extraction_provider=self._client.provider_name,
            extraction_model=self._client.model_name,
            raw_payload=normalized_raw_payload,
        )


_FOOD_BASE_UNIT_ALIASES = {
    "g": "g",
    "гр": "g",
    "г": "g",
    "gram": "g",
    "grams": "g",
    "ml": "ml",
    "мл": "ml",
}

_FOOD_LIQUID_UNIT_ALIASES = {
    "стакан": "glass",
    "стакана": "glass",
    "стаканов": "glass",
    "чашка": "cup",
    "чашки": "cup",
    "чашек": "cup",
    "кружка": "mug",
    "кружки": "mug",
    "кружек": "mug",
}

_FOOD_LIQUID_UNIT_ML_FACTORS = {
    "glass": 250,
    "cup": 250,
    "mug": 250,
}

_FOOD_PIECE_UNIT_ALIASES = {
    "шт",
    "шт.",
    "штука",
    "штуки",
    "штук",
    "piece",
    "pieces",
}

_FOOD_LIQUID_NAME_MARKERS = (
    "сок",
    "морс",
    "компот",
    "лимонад",
    "смузи",
    "кефир",
    "молоко",
    "йогурт",
    "айран",
    "ряженка",
    "какао",
    "кофе",
    "чай",
    "бульон",
    "суп",
)

_FOOD_PIECE_GRAM_FACTORS = (
    (("яйц",), 50),
    (("сосиск",), 60),
)


def _normalize_llm_raw_payload(raw_payload: str) -> str:
    try:
        parsed_payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return raw_payload

    if not isinstance(parsed_payload, dict):
        return raw_payload

    entries = parsed_payload.get("entries")
    if not isinstance(entries, list):
        return raw_payload

    normalized = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        items = entry.get("items")
        if not isinstance(items, list):
            continue

        if entry_type == "workout":
            for item in items:
                if not isinstance(item, dict):
                    continue
                metrics = item.get("metrics")
                if not isinstance(metrics, list):
                    continue
                filtered_metrics = [
                    metric
                    for metric in metrics
                    if isinstance(metric, dict) and metric.get("code") == "workout_calories"
                ]
                if filtered_metrics != metrics:
                    item["metrics"] = filtered_metrics
                    normalized = True
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            if "metrics" not in item:
                pass
            else:
                item.pop("metrics", None)
                normalized = True

            if entry_type != "food":
                continue

            quantity = item.get("quantity")
            unit = item.get("unit")
            if quantity is None or unit is None:
                continue

            normalized_quantity, normalized_unit = _normalize_food_quantity_unit(
                name=str(item.get("name", "")),
                quantity=quantity,
                unit=unit,
            )
            if normalized_quantity is not None and normalized_unit is not None:
                if normalized_quantity != quantity or normalized_unit != unit:
                    item["quantity"] = normalized_quantity
                    item["unit"] = normalized_unit
                    normalized = True
                continue

            item.pop("quantity", None)
            item.pop("unit", None)
            normalized = True

    if not normalized:
        return raw_payload

    return json.dumps(parsed_payload, ensure_ascii=False)


def _normalize_food_quantity_unit(
    *,
    name: str,
    quantity: object,
    unit: object,
) -> tuple[int | None, str | None]:
    if not isinstance(quantity, int) or quantity <= 0:
        return None, None

    normalized_name = name.strip().lower()
    normalized_unit = str(unit).strip().lower()

    base_unit = _FOOD_BASE_UNIT_ALIASES.get(normalized_unit)
    if base_unit is not None:
        return quantity, base_unit

    liquid_unit = _FOOD_LIQUID_UNIT_ALIASES.get(normalized_unit)
    liquid_factor = _FOOD_LIQUID_UNIT_ML_FACTORS.get(liquid_unit) if liquid_unit is not None else None
    if liquid_factor is not None and _looks_like_liquid_food(normalized_name):
        return quantity * liquid_factor, "ml"

    if normalized_unit in _FOOD_PIECE_UNIT_ALIASES:
        for markers, grams_per_piece in _FOOD_PIECE_GRAM_FACTORS:
            if any(marker in normalized_name for marker in markers):
                return quantity * grams_per_piece, "g"

    return None, None


def _looks_like_liquid_food(name: str) -> bool:
    return any(marker in name for marker in _FOOD_LIQUID_NAME_MARKERS)
