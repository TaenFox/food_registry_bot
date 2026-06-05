from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Protocol, Union

from pydantic import ValidationError

from food_registry_bot.diet.contract import DietEvaluationPayload, DietEvaluationRequest
from food_registry_bot.diet.llm_client import LLMDietClientError


@dataclass(frozen=True)
class ValidDietEvaluationPayload:
    payload: DietEvaluationPayload
    provider: str
    model: Optional[str]
    raw_payload: str


@dataclass(frozen=True)
class InvalidDietEvaluationPayload:
    message: str
    provider: Optional[str] = None
    model: Optional[str] = None
    raw_payload: Optional[str] = None
    technical_message: Optional[str] = None
    error_code: Optional[str] = None
    is_llm: bool = False


class DietPayloadClient(Protocol):
    provider_name: str
    model_name: Optional[str]

    def evaluate_diet_payload(self, request: DietEvaluationRequest) -> str:
        """Return a raw JSON diet evaluation payload for the provided items."""


class DietEvaluationService(Protocol):
    def evaluate(
        self, request: DietEvaluationRequest
    ) -> Union[ValidDietEvaluationPayload, InvalidDietEvaluationPayload]:
        """Estimate diet scores for normalized journal items."""


def _validate_request_coverage(*, request: DietEvaluationRequest, payload: DietEvaluationPayload) -> None:
    requested_item_ids = {item.client_item_id for item in request.items}
    returned_item_ids = {item.client_item_id for item in payload.items}
    if requested_item_ids != returned_item_ids:
        raise ValueError("diet payload must cover exactly the requested client_item_id set")

    requested_metric_codes = {diet.metric_code for diet in request.diets}
    for item in payload.items:
        returned_metric_codes = {score.code for score in item.scores}
        if returned_metric_codes != requested_metric_codes:
            raise ValueError("diet payload must include exactly one score for each requested diet metric code")


def _parse_payload(*, raw_payload: str, request: DietEvaluationRequest) -> DietEvaluationPayload:
    payload = DietEvaluationPayload.model_validate_json(raw_payload)
    _validate_request_coverage(request=request, payload=payload)
    return payload


class DisabledDietEvaluationService:
    def evaluate(
        self, request: DietEvaluationRequest
    ) -> Union[ValidDietEvaluationPayload, InvalidDietEvaluationPayload]:
        _ = request
        return InvalidDietEvaluationPayload(
            message="Diet evaluation service is disabled.",
            provider="disabled",
            error_code="service_disabled",
        )


class StaticDietEvaluationService:
    def __init__(
        self,
        *,
        raw_payload: str,
        provider_name: str = "static_diet_stub",
        model_name: Optional[str] = None,
    ) -> None:
        self._raw_payload = raw_payload
        self._provider_name = provider_name
        self._model_name = model_name

    def evaluate(
        self, request: DietEvaluationRequest
    ) -> Union[ValidDietEvaluationPayload, InvalidDietEvaluationPayload]:
        if not self._raw_payload.strip():
            return InvalidDietEvaluationPayload(
                message="Diet service вернул пустой structured payload.",
                provider=self._provider_name,
                model=self._model_name,
                raw_payload=self._raw_payload,
                technical_message="Static diet payload is empty",
                error_code="empty_payload",
            )

        try:
            payload = _parse_payload(raw_payload=self._raw_payload, request=request)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            return InvalidDietEvaluationPayload(
                message=(
                    "Diet service вернул невалидный structured payload. "
                    "Ожидаю объект вида {'items': [...]} с client_item_id и scores[]."
                ),
                provider=self._provider_name,
                model=self._model_name,
                raw_payload=self._raw_payload,
                technical_message=str(exc),
                error_code="invalid_payload",
            )

        return ValidDietEvaluationPayload(
            payload=payload,
            provider=self._provider_name,
            model=self._model_name,
            raw_payload=self._raw_payload,
        )


class LLMDietEvaluationService:
    def __init__(self, client: DietPayloadClient) -> None:
        self._client = client

    def evaluate(
        self, request: DietEvaluationRequest
    ) -> Union[ValidDietEvaluationPayload, InvalidDietEvaluationPayload]:
        try:
            raw_payload = self._client.evaluate_diet_payload(request)
        except LLMDietClientError as exc:
            return InvalidDietEvaluationPayload(
                message="Не удалось получить structured payload от diet provider.",
                provider=self._client.provider_name,
                model=self._client.model_name,
                technical_message=str(exc),
                error_code="client_error",
                is_llm=True,
            )

        if not raw_payload.strip():
            return InvalidDietEvaluationPayload(
                message="Diet provider вернул пустой structured payload.",
                provider=self._client.provider_name,
                model=self._client.model_name,
                raw_payload=raw_payload,
                technical_message="LLM returned an empty diet response",
                error_code="empty_payload",
                is_llm=True,
            )

        try:
            payload = _parse_payload(raw_payload=raw_payload, request=request)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            return InvalidDietEvaluationPayload(
                message=(
                    "Diet provider вернул невалидный structured payload. "
                    "Ожидаю объект вида {'items': [...]} с client_item_id и scores[]."
                ),
                provider=self._client.provider_name,
                model=self._client.model_name,
                raw_payload=raw_payload,
                technical_message=str(exc),
                error_code="invalid_payload",
                is_llm=True,
            )

        return ValidDietEvaluationPayload(
            payload=payload,
            provider=self._client.provider_name,
            model=self._client.model_name,
            raw_payload=raw_payload,
        )
