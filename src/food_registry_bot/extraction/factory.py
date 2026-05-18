from __future__ import annotations

from food_registry_bot.config import ExtractionProvider, Settings
from food_registry_bot.extraction.llm_client import LLMExtractionClient
from food_registry_bot.extraction.openai_client import OpenAIResponsesExtractionClient
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
        if not settings.openai_api_key:
            raise RuntimeError("EXTRACTION_PROVIDER=llm requires OPENAI_API_KEY")
        llm_client = OpenAIResponsesExtractionClient(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
        )

    return LLMExtractionService(client=llm_client)
