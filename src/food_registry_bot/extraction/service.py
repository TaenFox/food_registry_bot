from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from food_registry_bot.extraction.contract import ExtractedJournalPayload


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
