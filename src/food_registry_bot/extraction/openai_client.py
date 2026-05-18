from __future__ import annotations

import base64
import json
from typing import Any

from food_registry_bot.extraction.contract import ExtractedJournalPayload
from food_registry_bot.extraction.llm_client import LLMExtractionClientError
from food_registry_bot.extraction.request import JournalExtractionRequest


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

    def extract_journal_payload(self, request: JournalExtractionRequest) -> str:
        if not request.text and not request.images:
            raise LLMExtractionClientError("Cannot extract journal payload from empty text")

        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=self._build_system_prompt(),
                input=[{"role": "user", "content": self._build_user_content(request)}],
                text={"format": {"type": "json_object"}},
            )
        except Exception as exc:
            raise LLMExtractionClientError("OpenAI extraction request failed") from exc

        output_text = getattr(response, "output_text", None)
        if not output_text or not output_text.strip():
            raise LLMExtractionClientError("OpenAI returned an empty extraction response")

        return output_text

    @staticmethod
    def _build_user_content(request: JournalExtractionRequest) -> list[dict[str, str]]:
        content: list[dict[str, str]] = [
            {
                "type": "input_text",
                "text": (
                    "Return valid json only. "
                    "The response must be a json object with an entries array."
                ),
            }
        ]

        if request.text:
            content.append({"type": "input_text", "text": request.text})
        else:
            content.append(
                {
                    "type": "input_text",
                    "text": "Extract journal entries from this food photo.",
                }
            )

        for image in request.images:
            encoded_image = base64.b64encode(image.data).decode("utf-8")
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{image.media_type};base64,{encoded_image}",
                }
            )

        return content

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
            "You extract journal entries from a user message or food photo. "
            "Return json only, with no markdown and no explanatory text. "
            "The json must match this schema exactly: "
            f"{json.dumps(schema, ensure_ascii=False)}. "
            "Use 'food' or 'water' for entry type. "
            "If one message contains both food and water, return separate entries. "
            "If quantity is missing, omit quantity and unit. "
            "If time is not clearly stated, omit occurred_at. "
            "For food photos, extract only what is visible or clearly implied by caption text."
        )
