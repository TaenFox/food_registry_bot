from __future__ import annotations

from typing import Protocol

from food_registry_bot.extraction.request import JournalExtractionRequest


class LLMExtractionClientError(RuntimeError):
    pass


class LLMExtractionClient(Protocol):
    def extract_journal_payload(self, request: JournalExtractionRequest) -> str:
        """Return a JSON string matching the extraction contract."""
