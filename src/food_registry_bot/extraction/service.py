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
        except (ValidationError, json.JSONDecodeError):
            return InvalidExtractionPayload(
                message=(
                    "Не удалось разобрать structured payload. "
                    "Ожидаю объект вида {'entries': [...]} с type, items и name."
                )
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
        except LLMExtractionClientError:
            return InvalidExtractionPayload(
                message="Не удалось получить structured payload от LLM."
            )

        if not raw_payload.strip():
            return InvalidExtractionPayload(
                message="LLM вернула пустой structured payload для записи журнала."
            )

        try:
            payload = ExtractedJournalPayload.model_validate_json(raw_payload)
        except (ValidationError, json.JSONDecodeError):
            return InvalidExtractionPayload(
                message=(
                    "LLM вернула невалидный structured payload. "
                    "Ожидаю объект вида {'entries': [...]} с type, items и name."
                )
            )

        return ValidExtractionPayload(
            payload=payload,
            extraction_provider=self._client.provider_name,
            extraction_model=self._client.model_name,
            raw_payload=raw_payload,
        )
