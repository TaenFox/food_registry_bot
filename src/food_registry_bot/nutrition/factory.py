from __future__ import annotations

from food_registry_bot.config import NutritionProvider, Settings
from food_registry_bot.nutrition.llm_client import LLMNutritionClient
from food_registry_bot.nutrition.openai_client import OpenAIResponsesNutritionClient
from food_registry_bot.nutrition.service import (
    LLMNutritionEstimationService,
    NutritionEstimationService,
    StaticNutritionEstimationService,
)


def create_nutrition_service(
    settings: Settings,
    *,
    llm_client: LLMNutritionClient | None = None,
    static_raw_payload: str | None = None,
) -> NutritionEstimationService:
    if settings.nutrition_provider == NutritionProvider.STATIC:
        return StaticNutritionEstimationService(raw_payload=static_raw_payload or "")

    if llm_client is None:
        if not settings.openai_api_key:
            raise RuntimeError("NUTRITION_PROVIDER=llm requires OPENAI_API_KEY")
        llm_client = OpenAIResponsesNutritionClient(
            api_key=settings.openai_api_key,
            model=settings.nutrition_model,
        )

    return LLMNutritionEstimationService(client=llm_client)
