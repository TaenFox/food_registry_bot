from __future__ import annotations

from typing import Protocol

from food_registry_bot.nutrition.contract import NutritionEstimationRequest


class LLMNutritionClientError(RuntimeError):
    pass


class LLMNutritionClient(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_name(self) -> str | None:
        ...

    def estimate_nutrition_payload(self, request: NutritionEstimationRequest) -> str:
        """Return a JSON string matching the nutrition contract."""
