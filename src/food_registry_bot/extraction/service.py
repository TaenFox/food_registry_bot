from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from food_registry_bot.extraction.contract import ExtractedJournalPayload
from food_registry_bot.extraction.llm_client import LLMExtractionClient, LLMExtractionClientError


@dataclass(frozen=True)
class ValidExtractionPayload:
    payload: ExtractedJournalPayload


@dataclass(frozen=True)
class InvalidExtractionPayload:
    message: str


class JournalExtractionService(Protocol):
    def extract_from_text(
        self, message_text: str
    ) -> ValidExtractionPayload | InvalidExtractionPayload | None:
        """Extract a structured journal payload or return None when extraction is not attempted."""


class StructuredPayloadExtractionService:
    def extract_from_text(
        self, message_text: str
    ) -> ValidExtractionPayload | InvalidExtractionPayload | None:
        stripped_text = message_text.strip()
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

        return ValidExtractionPayload(payload=payload)


class LLMExtractionService:
    def __init__(self, client: LLMExtractionClient) -> None:
        self._client = client

    def extract_from_text(
        self, message_text: str
    ) -> ValidExtractionPayload | InvalidExtractionPayload | None:
        try:
            raw_payload = self._client.extract_journal_payload(message_text)
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

        return ValidExtractionPayload(payload=payload)
