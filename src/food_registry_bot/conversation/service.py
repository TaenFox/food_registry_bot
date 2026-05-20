from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from food_registry_bot.conversation.context import NutritionCoachFactualContext
from food_registry_bot.conversation.llm_client import (
    LLMConversationClient,
    LLMConversationClientError,
)


@dataclass(frozen=True)
class ConversationReply:
    text: str
    provider: str
    model: str | None


class ConversationService(Protocol):
    def reply(self, *, user_message: str, factual_context: NutritionCoachFactualContext) -> ConversationReply:
        """Return a conversational reply."""


class DisabledConversationService:
    def reply(self, *, user_message: str, factual_context: NutritionCoachFactualContext) -> ConversationReply:
        _ = user_message
        _ = factual_context
        return ConversationReply(
            text=(
                "Nutrition coach пока не настроен. "
                "Если хочешь использовать разговорный режим с учётом текущих метрик дня, добавь OPENAI_API_KEY."
            ),
            provider="disabled",
            model=None,
        )


class LLMConversationService:
    def __init__(self, client: LLMConversationClient) -> None:
        self._client = client

    def reply(self, *, user_message: str, factual_context: NutritionCoachFactualContext) -> ConversationReply:
        try:
            text = self._client.generate_reply(
                user_message=user_message,
                factual_context=factual_context,
            )
        except LLMConversationClientError:
            return ConversationReply(
                text="Не удалось получить ответ nutrition coach.",
                provider=self._client.provider_name,
                model=self._client.model_name,
            )

        return ConversationReply(
            text=text,
            provider=self._client.provider_name,
            model=self._client.model_name,
        )
