from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from food_registry_bot.nutrition.contract import NutritionEstimationPayload, NutritionEstimationRequest
from food_registry_bot.nutrition.llm_client import LLMNutritionClientError


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
