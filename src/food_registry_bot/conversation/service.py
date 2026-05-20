from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from food_registry_bot.conversation.context import (
    NutritionCoachConversationTurn,
    NutritionCoachFactualContext,
)
from food_registry_bot.conversation.llm_client import (
    LLMConversationClient,
    LLMConversationClientError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConversationReply:
    text: str
    provider: str
    model: str | None
    updated_session_summary: str | None = None


class ConversationService(Protocol):
    def reply(
        self,
        *,
        user_message: str,
        factual_context: NutritionCoachFactualContext,
        session_summary: str | None,
        recent_turns: list[NutritionCoachConversationTurn],
    ) -> ConversationReply:
        """Return a conversational reply."""


class DisabledConversationService:
    def reply(
        self,
        *,
        user_message: str,
        factual_context: NutritionCoachFactualContext,
        session_summary: str | None,
        recent_turns: list[NutritionCoachConversationTurn],
    ) -> ConversationReply:
        _ = user_message
        _ = factual_context
        _ = session_summary
        _ = recent_turns
        return ConversationReply(
            text=(
                "Nutrition coach пока не настроен. "
                "Если хочешь использовать разговорный режим с учётом текущих метрик дня, добавь OPENAI_API_KEY."
            ),
            provider="disabled",
            model=None,
            updated_session_summary=session_summary,
        )


class LLMConversationService:
    def __init__(self, client: LLMConversationClient) -> None:
        self._client = client

    def reply(
        self,
        *,
        user_message: str,
        factual_context: NutritionCoachFactualContext,
        session_summary: str | None,
        recent_turns: list[NutritionCoachConversationTurn],
    ) -> ConversationReply:
        try:
            text, updated_session_summary = self._client.generate_reply(
                user_message=user_message,
                factual_context=factual_context,
                session_summary=session_summary,
                recent_turns=recent_turns,
            )
        except LLMConversationClientError as exc:
            logger.exception("Nutrition coach request failed: %s", exc)
            return ConversationReply(
                text=f"Не удалось получить ответ nutrition coach. Причина: {exc}",
                provider=self._client.provider_name,
                model=self._client.model_name,
                updated_session_summary=session_summary,
            )

        return ConversationReply(
            text=text,
            provider=self._client.provider_name,
            model=self._client.model_name,
            updated_session_summary=updated_session_summary,
        )
