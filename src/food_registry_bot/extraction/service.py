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
        if entry.get("type") == "workout":
            continue

        items = entry.get("items")
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            if "metrics" not in item:
                continue
            item.pop("metrics", None)
            normalized = True

    if not normalized:
        return raw_payload

    return json.dumps(parsed_payload, ensure_ascii=False)
