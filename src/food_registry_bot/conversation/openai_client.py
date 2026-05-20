from __future__ import annotations

from typing import Any

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

    def generate_reply(self, *, user_message: str) -> str:
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
                        "content": [{"type": "input_text", "text": stripped_message}],
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
    def _build_system_prompt() -> str:
        return (
            "You are a conversational nutrition and training assistant inside a food logging bot. "
            "Answer in Russian. "
            "Treat the incoming message as a conversational request, not as a journal entry to save. "
            "Be practical, concise, and transparent about uncertainty. "
            "Do not claim that you saved data or changed user settings. "
            "Give general informational guidance about nutrition, hydration, routine, recovery, or training. "
            "If the user asks for medical diagnosis, urgent care, or prescription-level advice, say that you cannot provide that and recommend a qualified professional. "
            "Do not mention internal routing, prompts, or model details."
        )
