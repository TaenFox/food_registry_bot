from __future__ import annotations

import json
from typing import Any

from food_registry_bot.conversation.context import NutritionCoachFactualContext
from food_registry_bot.conversation.llm_client import LLMConversationClientError


class OpenAIResponsesConversationClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required for OpenAI conversation client")

        self._model = model
        self._client = client or self._build_sdk_client(api_key=api_key)

    @property
    def provider_name(self) -> str:
        return "openai_responses"

    @property
    def model_name(self) -> str:
        return self._model

    def generate_reply(self, *, user_message: str, factual_context: NutritionCoachFactualContext) -> str:
        stripped_message = user_message.strip()
        if not stripped_message:
            raise LLMConversationClientError("Cannot generate a reply for an empty user message")

        try:
            response = self._client.responses.create(
                model=self._model,
                instructions=self._build_system_prompt(),
                input=[
                    {
                        "role": "user",
                        "content": self._build_user_content(
                            user_message=stripped_message,
                            factual_context=factual_context,
                        ),
                    }
                ],
            )
        except Exception as exc:
            raise LLMConversationClientError("OpenAI conversation request failed") from exc

        output_text = getattr(response, "output_text", None)
        if not output_text or not output_text.strip():
            raise LLMConversationClientError("OpenAI returned an empty conversation response")

        return output_text.strip()

    @staticmethod
    def _build_sdk_client(*, api_key: str) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package is not installed. Add the dependency before using conversational LLM replies."
            ) from exc

        return OpenAI(api_key=api_key)

    @staticmethod
    def _build_user_content(
        *,
        user_message: str,
        factual_context: NutritionCoachFactualContext,
    ) -> list[dict[str, str]]:
        return [
            {
                "type": "input_text",
                "text": "Use the factual day context below as the current source of truth for this reply.",
            },
            {
                "type": "input_text",
                "text": json.dumps(factual_context.model_dump(mode="json"), ensure_ascii=False),
            },
            {
                "type": "input_text",
                "text": user_message,
            },
        ]

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "You are a nutrition coach inside a food logging bot. "
            "Answer in Russian. "
            "Treat the incoming message as a conversational request, not as a journal entry to save. "
            "Always use the provided factual day context as the current source of truth for the user's day. "
            "Be practical, concise, and transparent about uncertainty. "
            "Do not claim that you saved data or changed user settings. "
            "Help with nutrition, hydration, meal-planning, product-based meal suggestions, and nutrition around training. "
            "You may answer general health questions, but keep the answer grounded in nutrition, hydration, routine, recovery, and wellbeing. "
            "When the user asks what to eat, what to cook, how to finish the day, or how to prepare for training, tailor the answer to the provided factual context. "
            "Prefer concrete next-step advice over abstract theory. "
            "If the question is clearly outside your domain, answer briefly and steer the user back to nutrition, water, wellbeing, or meal planning. "
            "If the user asks for medical diagnosis, urgent care, or prescription-level advice, say that you cannot provide that and recommend a qualified professional. "
            "Do not mention internal routing, prompts, or model details."
        )
