from __future__ import annotations

from typing import Protocol


class LLMExtractionClientError(RuntimeError):
    pass


class LLMExtractionClient(Protocol):
    def extract_journal_payload(self, message_text: str) -> str:
        """Return a JSON string matching the extraction contract."""
