from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from typing import Protocol

from pydantic import ValidationError

from food_registry_bot.nutrition.contract import (
    NutritionEstimationItemResult,
    NutritionEstimationPayload,
    NutritionEstimationRequest,
)
from food_registry_bot.nutrition.llm_client import LLMNutritionClientError


ENERGY_MISMATCH_TOLERANCE = 0.07
_ALCOHOL_NAME = re.compile(
    r"\b(?:пив(?:о|а|у|ом|е)|вин(?:о|а|у|ом|е)|виски|водк\w*|коньяк\w*|ром(?:а|ом|е)?|"
    r"текил\w*|джин(?:а|ом|е)?|лик[её]р\w*|шампанск\w*|просекко|сидр\w*|вермут\w*|"
    r"бренди|настойк\w*|наливк\w*|эль|портвейн\w*|мартини|абсент\w*|саке|"
    r"beer|wine|whiske?y|vodka|brandy|rum|tequila|gin|liqueur|champagne|prosecco|cider|"
    r"vermouth|ale|martini|absinthe|sake)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ValidNutritionPayload:
    payload: NutritionEstimationPayload
    nutrition_provider: str
    nutrition_model: str | None
    raw_payload: str


@dataclass(frozen=True)
class InvalidNutritionPayload:
    message: str
    provider: str | None = None
    model: str | None = None
    raw_payload: str | None = None
    technical_message: str | None = None
    error_code: str | None = None
    is_llm: bool = False


class NutritionPayloadClient(Protocol):
    provider_name: str
    model_name: str | None

    def estimate_nutrition_payload(self, request: NutritionEstimationRequest) -> str:
        """Return a raw JSON nutrition payload for the provided normalized items."""


class NutritionEstimationService(Protocol):
    def estimate(
        self, request: NutritionEstimationRequest
    ) -> ValidNutritionPayload | InvalidNutritionPayload:
        """Estimate nutrition for normalized journal items."""


def _validate_request_coverage(
    *,
    request: NutritionEstimationRequest,
    payload: NutritionEstimationPayload,
) -> None:
    requested_item_ids = {item.client_item_id for item in request.items}
    returned_item_ids = {item.client_item_id for item in payload.items}
    if requested_item_ids != returned_item_ids:
        raise ValueError("nutrition payload must cover exactly the requested client_item_id set")


def _parse_payload(
    *,
    raw_payload: str,
    request: NutritionEstimationRequest,
) -> NutritionEstimationPayload:
    payload = NutritionEstimationPayload.model_validate_json(raw_payload)
    _validate_request_coverage(request=request, payload=payload)
    return payload


def _energy_mismatch(item: NutritionEstimationItemResult) -> float | None:
    values = {metric.code: metric.value for metric in item.metrics}
    calories = values["calories"]
    from_macros = 4 * values["protein"] + 9 * values["fat"] + 4 * values["carbs"]
    if not (0 < calories < math.inf and 0 < from_macros < math.inf):
        return None
    return abs(from_macros - calories) / calories


class StaticNutritionEstimationService:
    def __init__(
        self,
        *,
        raw_payload: str,
        provider_name: str = "static_nutrition_stub",
        model_name: str | None = None,
    ) -> None:
        self._raw_payload = raw_payload
        self._provider_name = provider_name
        self._model_name = model_name

    def estimate(
        self, request: NutritionEstimationRequest
    ) -> ValidNutritionPayload | InvalidNutritionPayload:
        if not self._raw_payload.strip():
            return InvalidNutritionPayload(
                message="Nutrition service вернул пустой structured payload.",
                provider=self._provider_name,
                model=self._model_name,
                raw_payload=self._raw_payload,
                technical_message="Static nutrition payload is empty",
                error_code="empty_payload",
            )

        try:
            payload = _parse_payload(raw_payload=self._raw_payload, request=request)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            return InvalidNutritionPayload(
                message=(
                    "Nutrition service вернул невалидный structured payload. "
                    "Ожидаю объект вида {'items': [...]} с client_item_id и metrics[]."
                ),
                provider=self._provider_name,
                model=self._model_name,
                raw_payload=self._raw_payload,
                technical_message=str(exc),
                error_code="invalid_payload",
            )

        return ValidNutritionPayload(
            payload=payload,
            nutrition_provider=self._provider_name,
            nutrition_model=self._model_name,
            raw_payload=self._raw_payload,
        )


class LLMNutritionEstimationService:
    def __init__(self, client: NutritionPayloadClient) -> None:
        self._client = client

    def estimate(
        self, request: NutritionEstimationRequest
    ) -> ValidNutritionPayload | InvalidNutritionPayload:
        first = self._estimate_once(request)
        if isinstance(first, InvalidNutritionPayload):
            return first

        eligible_ids = {
            item.client_item_id for item in request.items
            if item.nutrition_label is None and not _ALCOHOL_NAME.search(item.name)
        }
        mismatches = {}
        for item in first.payload.items:
            if item.client_item_id not in eligible_ids:
                continue
            mismatch = _energy_mismatch(item)
            if mismatch is not None and mismatch > ENERGY_MISMATCH_TOLERANCE:
                mismatches[item.client_item_id] = mismatch
        if not mismatches:
            return first

        retry_request = NutritionEstimationRequest(
            items=[item for item in request.items if item.client_item_id in mismatches]
        )
        retry = self._estimate_once(retry_request)
        if isinstance(retry, InvalidNutritionPayload):
            return first

        improved = {}
        for item in retry.payload.items:
            mismatch = _energy_mismatch(item)
            if mismatch is not None and mismatch < mismatches[item.client_item_id]:
                improved[item.client_item_id] = item
        if not improved:
            return first

        if len(improved) == len(first.payload.items):
            return retry
        payload = NutritionEstimationPayload(
            items=[improved.get(item.client_item_id, item) for item in first.payload.items]
        )
        return replace(first, payload=payload)

    def _estimate_once(
        self, request: NutritionEstimationRequest
    ) -> ValidNutritionPayload | InvalidNutritionPayload:
        try:
            raw_payload = self._client.estimate_nutrition_payload(request)
        except LLMNutritionClientError as exc:
            return InvalidNutritionPayload(
                message="Не удалось получить structured payload от nutrition provider.",
                provider=self._client.provider_name,
                model=self._client.model_name,
                technical_message=str(exc),
                error_code="client_error",
                is_llm=True,
            )

        if not raw_payload.strip():
            return InvalidNutritionPayload(
                message="Nutrition provider вернул пустой structured payload.",
                provider=self._client.provider_name,
                model=self._client.model_name,
                raw_payload=raw_payload,
                technical_message="OpenAI returned an empty nutrition response",
                error_code="empty_payload",
                is_llm=True,
            )

        try:
            payload = _parse_payload(raw_payload=raw_payload, request=request)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            return InvalidNutritionPayload(
                message=(
                    "Nutrition provider вернул невалидный structured payload. "
                    "Ожидаю объект вида {'items': [...]} с client_item_id и metrics[]."
                ),
                provider=self._client.provider_name,
                model=self._client.model_name,
                raw_payload=raw_payload,
                technical_message=str(exc),
                error_code="invalid_payload",
                is_llm=True,
            )

        return ValidNutritionPayload(
            payload=payload,
            nutrition_provider=self._client.provider_name,
            nutrition_model=self._client.model_name,
            raw_payload=raw_payload,
        )
