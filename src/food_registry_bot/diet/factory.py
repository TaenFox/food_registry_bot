from __future__ import annotations

from typing import Optional

from food_registry_bot.config import Settings
from food_registry_bot.diet.llm_client import LLMDietClient
from food_registry_bot.diet.mistral_client import MistralChatCompletionsDietClient
from food_registry_bot.diet.openai_client import OpenAIResponsesDietClient
from food_registry_bot.diet.service import (
    DietEvaluationService,
    DisabledDietEvaluationService,
    LLMDietEvaluationService,
    StaticDietEvaluationService,
)
from food_registry_bot.db.models import LLMProvider


def create_diet_service(
    settings: Settings,
    *,
    llm_client: Optional[LLMDietClient] = None,
    static_raw_payload: Optional[str] = None,
) -> DietEvaluationService:
    if llm_client is None and static_raw_payload is not None:
        return StaticDietEvaluationService(raw_payload=static_raw_payload)

    if llm_client is None:
        if not settings.openai_api_key:
            return DisabledDietEvaluationService()
        llm_client = OpenAIResponsesDietClient(
            api_key=settings.openai_api_key,
            model=settings.conversation_model,
        )

    return LLMDietEvaluationService(client=llm_client)


def create_diet_service_for_provider_access(
    *,
    provider: Optional[str],
    api_key: Optional[str],
    model: Optional[str],
    settings: Settings,
) -> DietEvaluationService:
    if provider == LLMProvider.OPENAI.value and api_key and model:
        return create_diet_service(
            settings,
            llm_client=OpenAIResponsesDietClient(api_key=api_key, model=model),
        )
    if provider == LLMProvider.MISTRAL.value and api_key and model:
        return create_diet_service(
            settings,
            llm_client=MistralChatCompletionsDietClient(api_key=api_key, model=model),
        )
    return DisabledDietEvaluationService()
