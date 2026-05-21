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

    @property
    def provider_name(self) -> str:
        return "openai_responses"

    @property
    def model_name(self) -> str:
        return self._model

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
            "Extract journal entries from a user message or food photo. "
            "Return only valid json matching this schema exactly: "
            f"{json.dumps(schema, ensure_ascii=False)}. "
            "Use 'food', 'water', or 'workout' for entry type and keep separate entries when one message contains mixed journal facts. "
            "For water entries, always set item.name to exactly 'water'. "
            "If water quantity is present, use unit 'ml'. "
            "For workout entries, item.name should be the workout or activity name. "
            "If workout duration is explicitly stated, use item.quantity with unit 'min'. "
            "Do not invent workout calories, pulse, or other derived metrics. "
            "Return item names in Russian unless a fixed brand or label should stay unchanged. "
            "For food photos, if one complete dish is shown, save it as one item and do not decompose it into guessed ingredients. "
            "Split into multiple items only when separate foods are clearly shown separately or explicitly listed by the user. "
            "If quantity is estimated, prefer grams for food and milliliters for water or drinks; for liquid food items like dipping sauces, milliliters are also allowed. "
            "Do not return a bare number without a unit. "
            "For solid food use grams, for water or drinks use milliliters, for liquid sauces use milliliters, and for workout duration use minutes. "
            "Avoid vague units like portion, piece, slice, spoon, or serving when grams or milliliters can be reasonably estimated. "
            "For foods made of several visible pieces of the same dish, estimate the weight of one piece first and then sum them into one total gram value. "
            "If a dipping sauce is served separately and clearly visible, save it as a separate item and estimate it in milliliters. "
            "For photo-based food estimates, if exact mass is unclear, round to a reasonable step such as 25 grams instead of pretending to know exact precision. "
            "If quantity is not clear, omit quantity and unit. "
            "If time is not clearly stated, omit occurred_at. "
            "Use only what is visible in the photo or clearly stated in the text/caption."
        )
