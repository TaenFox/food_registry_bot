from __future__ import annotations

from food_registry_bot.config import ExtractionProvider, Settings
from food_registry_bot.extraction.llm_client import LLMExtractionClient
from food_registry_bot.extraction.service import (
    JournalExtractionService,
    LLMExtractionService,
    StructuredPayloadExtractionService,
)


def create_extraction_service(
    settings: Settings,
    *,
    llm_client: LLMExtractionClient | None = None,
) -> JournalExtractionService:
    if settings.extraction_provider == ExtractionProvider.STRUCTURED_PAYLOAD:
        return StructuredPayloadExtractionService()

    if llm_client is None:
        raise RuntimeError("EXTRACTION_PROVIDER=llm requires an LLM extraction client")

    return LLMExtractionService(client=llm_client)
