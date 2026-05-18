from __future__ import annotations

from typing import Protocol

from food_registry_bot.extraction.request import JournalExtractionRequest


class LLMExtractionClientError(RuntimeError):
    pass


class LLMExtractionClient(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_name(self) -> str:
        ...

    def extract_journal_payload(self, request: JournalExtractionRequest) -> str:
        """Return a JSON string matching the extraction contract."""
