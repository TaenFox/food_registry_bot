from __future__ import annotations

import json
from typing import Any, Optional

from food_registry_bot.diet.contract import DietEvaluationPayload, DietEvaluationRequest
from food_registry_bot.diet.llm_client import LLMDietClientError


class OpenAIResponsesDietClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: Optional[Any] = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required for OpenAI diet client")

        self._model = model
        self._client = client or self._build_sdk_client(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "openai_responses"

    @property
    def model_name(self) -> str:
        return self._model

    def evaluate_diet_payload(self, request: DietEvaluationRequest) -> str:
        if not request.items:
            raise LLMDietClientError("Cannot evaluate diet for an empty item list")

        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=self._build_system_prompt(),
                input=[{"role": "user", "content": self._build_user_content(request)}],
                text={"format": {"type": "json_object"}},
            )
        except Exception as exc:
            raise LLMDietClientError("OpenAI diet request failed") from exc

        output_text = getattr(response, "output_text", None)
        if not output_text or not output_text.strip():
            raise LLMDietClientError("OpenAI returned an empty diet response")

        return output_text

    @staticmethod
    def _build_user_content(request: DietEvaluationRequest) -> list[dict[str, str]]:
        return [
            {
                "type": "input_text",
                "text": "Return valid json only. The response must be a json object with an items array.",
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
                "openai package is not installed. Add the dependency before using diet evaluation."
            ) from exc

        return OpenAI(api_key=api_key)

    @staticmethod
    def _build_system_prompt() -> str:
        schema = DietEvaluationPayload.model_json_schema()
        return (
            "Evaluate how well each provided item fits each requested diet. "
            "Return only valid json matching this schema exactly: "
            f"{json.dumps(schema, ensure_ascii=False)}. "
            "Each response item must keep the same client_item_id as in the request. "
            "Return one result for every request item and do not omit or invent client_item_id values. "
            "For every item return one score entry for every requested diet metric code. "
            "Scores use a 1-10 scale where 1 is strongly incompatible, 5 is neutral, and 10 is strongly compatible. "
            "Use only low, medium, or high for confidence. "
            "Use the provided quantity and unit when present. "
            "The score must reflect compatibility of the consumed amount, not only the generic product category. "
            "A larger problematic portion should usually score worse than a smaller one, "
            "and a larger beneficial portion should usually score better than a smaller one. "
            "Do not add explanations, ranges, or extra fields."
        )
