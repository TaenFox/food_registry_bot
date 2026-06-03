from __future__ import annotations

import json
from typing import Optional

from food_registry_bot.diet.contract import DietEvaluationPayload, DietEvaluationRequest
from food_registry_bot.diet.llm_client import LLMDietClientError
from food_registry_bot.mistral_chat import (
    MistralChatClientError,
    MistralChatCompletionsClient,
    extract_mistral_message_text,
)


class MistralChatCompletionsDietClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: Optional[MistralChatCompletionsClient] = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("MISTRAL_API_KEY is required for Mistral diet client")

        self._model = model
        self._client = client or MistralChatCompletionsClient(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "mistral_chat_completions"

    @property
    def model_name(self) -> str:
        return self._model

    def evaluate_diet_payload(self, request: DietEvaluationRequest) -> str:
        if not request.items:
            raise LLMDietClientError("Cannot evaluate diet for an empty item list")

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
                        "name": "diet_evaluation",
                        "schema": DietEvaluationPayload.model_json_schema(),
                    },
                },
            )
        except MistralChatClientError as exc:
            raise LLMDietClientError(str(exc)) from exc

        output_text = extract_mistral_message_text(response_json).strip()
        if not output_text:
            raise LLMDietClientError("Mistral returned an empty diet response")
        return output_text

    @staticmethod
    def _build_user_content(request: DietEvaluationRequest) -> str:
        return json.dumps(request.model_dump(mode="json"), ensure_ascii=False)

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "Evaluate how well each provided item fits each requested diet. "
            "Return only the json payload and no extra text. "
            "Use a 1-10 compatibility score for each requested diet metric code. "
            "Water should score very high for low-purine diets when requested."
        )
