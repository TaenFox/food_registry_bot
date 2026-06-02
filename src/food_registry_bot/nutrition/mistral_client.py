from __future__ import annotations

import json

from food_registry_bot.mistral_chat import (
    MistralChatClientError,
    MistralChatCompletionsClient,
    extract_mistral_message_text,
)
from food_registry_bot.nutrition.contract import NutritionEstimationPayload, NutritionEstimationRequest
from food_registry_bot.nutrition.llm_client import LLMNutritionClientError


class MistralChatCompletionsNutritionClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: MistralChatCompletionsClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("MISTRAL_API_KEY is required for Mistral nutrition client")

        self._model = model
        self._client = client or MistralChatCompletionsClient(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "mistral_chat_completions"

    @property
    def model_name(self) -> str:
        return self._model

    def estimate_nutrition_payload(self, request: NutritionEstimationRequest) -> str:
        if not request.items:
            raise LLMNutritionClientError("Cannot estimate nutrition for an empty item list")

        try:
            response_json = self._client.complete(
                model=self._model,
                messages=[
                    {"role": "system", "content": self._build_system_prompt()},
                    {"role": "user", "content": self._build_user_content(request)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "nutrition_estimation_payload",
                        "schema": NutritionEstimationPayload.model_json_schema(),
                    },
                },
            )
        except MistralChatClientError as exc:
            raise LLMNutritionClientError(str(exc)) from exc

        output_text = extract_mistral_message_text(response_json).strip()
        if not output_text:
            raise LLMNutritionClientError("Mistral returned an empty nutrition response")
        return output_text

    @staticmethod
    def _build_user_content(request: NutritionEstimationRequest) -> str:
        return json.dumps(request.model_dump(mode="json"), ensure_ascii=False)

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "Estimate nutrition for each provided food item. "
            "Return only the nutrition payload object and do not echo any schema description. "
            "Each response item must keep the same client_item_id as in the request. "
            "Return one result for every request item and do not omit or invent client_item_id values. "
            "Return the metrics calories, protein, fat, carbs, and fiber for every item. "
            "Calories must be in kcal as a non-negative number and the other metrics must be non-negative numbers in grams. "
            "Return confidence for every metric using only low, medium, or high. "
            "Use the provided quantity and unit as the basis for the estimate when they are present. "
            "If nutrition_label is present for an item, treat it as structured label data extracted from the user's text or photo and use it as the primary factual source. "
            "When nutrition_label.basis is per_100g or per_100ml, convert it to the actual item quantity when quantity and unit are present. "
            "When nutrition_label.basis is per_serving, use serving_quantity and serving_unit from nutrition_label to scale the metrics if needed. "
            "When nutrition_label.basis is unknown, prefer the most plausible packaged-food convention from context, usually per 100 g for solid foods and per 100 ml for drinks, and lower confidence if you had to infer the basis. "
            "If nutrition_label provides calories, protein, fat, carbs, or fiber, do not overwrite those facts with unrelated generic estimates. "
            "If nutrition_label is missing fiber, estimate only the missing fiber value from the item context. "
            "If quantity and unit are missing, still estimate the metrics from the item name and use low confidence when the estimate is rough. "
            "Do not add explanations, ranges, or extra fields."
        )
