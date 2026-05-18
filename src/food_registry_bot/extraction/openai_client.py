from __future__ import annotations

import json
from typing import Any

from food_registry_bot.extraction.contract import ExtractedJournalPayload
from food_registry_bot.extraction.llm_client import LLMExtractionClientError


class OpenAIResponsesExtractionClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required for OpenAI extraction client")

        self._model = model
        self._client = client or self._build_sdk_client(api_key=api_key)

    def extract_journal_payload(self, message_text: str) -> str:
        if not message_text.strip():
            raise LLMExtractionClientError("Cannot extract journal payload from empty text")

        try:
            response = self._client.responses.create(
                model=self._model,
                input=[
                    {"role": "system", "content": self._build_system_prompt()},
                    {"role": "user", "content": message_text},
                ],
                text={"format": {"type": "json_object"}},
            )
        except Exception as exc:
            raise LLMExtractionClientError("OpenAI extraction request failed") from exc

        output_text = getattr(response, "output_text", None)
        if not output_text or not output_text.strip():
            raise LLMExtractionClientError("OpenAI returned an empty extraction response")

        return output_text

    @staticmethod
    def _build_sdk_client(*, api_key: str) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is not installed. Add the dependency before using EXTRACTION_PROVIDER=llm."
            ) from exc

        return OpenAI(api_key=api_key)

    @staticmethod
    def _build_system_prompt() -> str:
        schema = ExtractedJournalPayload.model_json_schema()
        return (
            "You extract journal entries from a user message. "
            "Return JSON only, with no markdown and no explanatory text. "
            "The JSON must match this schema exactly: "
            f"{json.dumps(schema, ensure_ascii=False)}. "
            "Use 'food' or 'water' for entry type. "
            "If one message contains both food and water, return separate entries. "
            "If quantity is missing, omit quantity and unit. "
            "If time is not clearly stated, omit occurred_at."
        )
