from __future__ import annotations

from typing import Optional, Protocol

from food_registry_bot.diet.contract import DietEvaluationRequest


class LLMDietClientError(RuntimeError):
    pass


class LLMDietClient(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_name(self) -> Optional[str]:
        ...

    def evaluate_diet_payload(self, request: DietEvaluationRequest) -> str:
        """Return a JSON string matching the diet evaluation contract."""
