from __future__ import annotations

import json
from typing import Any

from food_registry_bot.nutrition.contract import NutritionEstimationPayload, NutritionEstimationRequest
from food_registry_bot.nutrition.llm_client import LLMNutritionClientError


class OpenAIResponsesNutritionClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required for OpenAI nutrition client")

        self._model = model
        self._client = client or self._build_sdk_client(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "openai_responses"

    @property
    def model_name(self) -> str:
        return self._model

    def estimate_nutrition_payload(self, request: NutritionEstimationRequest) -> str:
        if not request.items:
            raise LLMNutritionClientError("Cannot estimate nutrition for an empty item list")

        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=self._build_system_prompt(),
                input=[{"role": "user", "content": self._build_user_content(request)}],
                text={"format": {"type": "json_object"}},
            )
        except Exception as exc:
            raise LLMNutritionClientError("OpenAI nutrition request failed") from exc

        output_text = getattr(response, "output_text", None)
        if not output_text or not output_text.strip():
            raise LLMNutritionClientError("OpenAI returned an empty nutrition response")

        return output_text

    @staticmethod
    def _build_user_content(request: NutritionEstimationRequest) -> list[dict[str, str]]:
        return [
            {
                "type": "input_text",
                "text": (
                    "Return valid json only. "
                    "The response must be a json object with an items array."
                ),
            },
            {
                "type": "input_text",
                "text": json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
            },
        ]

    @staticmethod
    def _build_sdk_client(*, api_key: str) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is not installed. Add the dependency before using NUTRITION_PROVIDER=llm."
            ) from exc

        return OpenAI(api_key=api_key)

    @staticmethod
    def _build_system_prompt() -> str:
        schema = NutritionEstimationPayload.model_json_schema()
        return (
            "Estimate nutrition for each provided food item. "
            "Return only valid json matching this schema exactly: "
            f"{json.dumps(schema, ensure_ascii=False)}. "
            "Each response item must keep the same client_item_id as in the request. "
            "Return one result for every request item and do not omit or invent client_item_id values. "
            "Calories must be in kcal as a non-negative integer. "
            "Protein, fat, and carbs must be non-negative numbers in grams. "
            "Use the provided quantity and unit as the basis for the estimate. "
            "Do not add explanations, confidence, ranges, or extra fields."
        )
