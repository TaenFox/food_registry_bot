from __future__ import annotations

import base64
import json

from food_registry_bot.extraction.contract import ExtractedJournalPayload
from food_registry_bot.extraction.llm_client import LLMExtractionClientError
from food_registry_bot.extraction.request import JournalExtractionRequest
from food_registry_bot.mistral_chat import (
    MistralChatClientError,
    MistralChatCompletionsClient,
    extract_mistral_message_text,
)


class MistralChatCompletionsExtractionClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: MistralChatCompletionsClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("MISTRAL_API_KEY is required for Mistral extraction client")

        self._model = model
        self._client = client or MistralChatCompletionsClient(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "mistral_chat_completions"

    @property
    def model_name(self) -> str:
        return self._model

    def extract_journal_payload(self, request: JournalExtractionRequest) -> str:
        if not request.text and not request.images:
            raise LLMExtractionClientError("Cannot extract journal payload from empty text")

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
                        "name": "extracted_journal_payload",
                        "schema": ExtractedJournalPayload.model_json_schema(),
                    },
                },
            )
        except MistralChatClientError as exc:
            raise LLMExtractionClientError(str(exc)) from exc

        output_text = extract_mistral_message_text(response_json).strip()
        if not output_text:
            raise LLMExtractionClientError("Mistral returned an empty extraction response")
        return output_text

    @staticmethod
    def _build_user_content(request: JournalExtractionRequest) -> list[dict[str, str]]:
        content: list[dict[str, str]] = [
            {
                "type": "text",
                "text": (
                    "Return valid json only. "
                    "The response must be a json object with an entries array."
                ),
            }
        ]
        if request.text:
            content.append({"type": "text", "text": request.text})
        else:
            content.append({"type": "text", "text": "Extract journal entries from this image."})

        for image in request.images:
            encoded_image = base64.b64encode(image.data).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": f"data:{image.media_type};base64,{encoded_image}",
                }
            )
        return content

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "Extract journal entries from a user message or food photo. "
            "Return only the extracted journal payload object and do not echo any schema description. "
            "Use 'food', 'water', or 'workout' for entry type and keep separate entries when one message contains mixed journal facts. "
            "For water entries, always set item.name to exactly 'water'. "
            "If water quantity is present, use unit 'ml'. "
            "For workout entries, item.name should be the workout or activity name. "
            "If workout duration is explicitly stated, use item.quantity with unit 'min'. "
            "If workout calories are explicitly visible and reliable, put them into item.metrics as code 'workout_calories'. "
            "Do not put nutrition metrics into food or water items. "
            "Do not return kcal, protein, fat, carbs, or similar nutrition fields inside item.metrics for food or water. "
            "Nutrition metrics for food are computed later by the nutrition layer. "
            "If the user's text or photo explicitly contains packaged-food nutrition label facts for a food item, put them into item.nutrition_label instead. "
            "nutrition_label must contain calories, protein, fat, and carbs, may additionally contain fiber, and must describe whether the label is per_100g, per_100ml, per_serving, or unknown. "
            "Use per_serving only when the serving size is explicit enough to fill serving_quantity and serving_unit. "
            "If a compact Russian notation like 'КБЖУ: 480/15/37/22' is explicitly provided but the basis is not visible, you may use nutrition_label.basis='unknown'. "
            "If the label basis is clearly visible on the package photo or in the text, prefer the explicit basis over unknown. "
            "Do not invent nutrition_label values or add nutrition_label when no explicit label facts are present. "
            "Do not invent workout calories, pulse, or other derived metrics. "
            "Return item names in Russian unless a fixed brand or label should stay unchanged. "
            "For food photos, if one complete dish is shown, save it as one item and do not decompose it into guessed ingredients. "
            "Split into multiple items only when separate foods are clearly shown separately or explicitly listed by the user. "
            "If quantity is estimated, prefer grams for food and milliliters for water or drinks; for liquid food items like dipping sauces, milliliters are also allowed. "
            "Do not return a bare number without a unit. "
            "For solid food use grams, for water or drinks use milliliters, for liquid sauces use milliliters, and for workout duration use minutes. "
            "If the user uses household units like glass, cup, mug, piece, or штука, convert them into estimated milliliters or grams instead of returning those units directly whenever a reasonable estimate is possible. "
            "Avoid vague units like portion, piece, slice, spoon, or serving when grams or milliliters can be reasonably estimated. "
            "For foods made of several visible pieces of the same dish, estimate the weight of one piece first and then sum them into one total gram value. "
            "If a dipping sauce is served separately and clearly visible, save it as a separate item and estimate it in milliliters. "
            "For photo-based food estimates, if exact mass is unclear, round to a reasonable step such as 25 grams instead of pretending to know exact precision. "
            "If quantity is not clear, omit quantity and unit. "
            "If time is not clearly stated, omit occurred_at. "
            "For workout screenshots, prefer saving one compact workout item rather than the full exercise list. "
            "Do not save screenshot exercise-by-exercise composition on this step. "
            "Use only what is visible in the photo or clearly stated in the text/caption."
        )
