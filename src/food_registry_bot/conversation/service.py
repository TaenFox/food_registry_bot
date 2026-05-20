from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from food_registry_bot.conversation.context import (
    NutritionCoachConversationTurn,
    NutritionCoachFactualContext,
)
from food_registry_bot.extraction.request import ExtractionImageInput
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
        images: tuple[ExtractionImageInput, ...] = (),
    ) -> ConversationReply:
        """Return a conversational reply."""

    def comment_on_food_write(
        self,
        *,
        saved_items: list[str],
        factual_context: NutritionCoachFactualContext,
        metric_deltas: dict[str, float],
    ) -> str | None:
        """Return a short optional post-entry comment."""


class DisabledConversationService:
    def reply(
        self,
        *,
        user_message: str,
        factual_context: NutritionCoachFactualContext,
        session_summary: str | None,
        recent_turns: list[NutritionCoachConversationTurn],
        images: tuple[ExtractionImageInput, ...] = (),
    ) -> ConversationReply:
        _ = user_message
        _ = factual_context
        _ = session_summary
        _ = recent_turns
        _ = images
        return ConversationReply(
            text=(
                "Режим консультации пока недоступен. "
                "Попробуй позже или продолжай пользоваться дневником записей."
            ),
            provider="disabled",
            model=None,
            updated_session_summary=session_summary,
        )

    def comment_on_food_write(
        self,
        *,
        saved_items: list[str],
        factual_context: NutritionCoachFactualContext,
        metric_deltas: dict[str, float],
    ) -> str | None:
        _ = saved_items
        _ = factual_context
        _ = metric_deltas
        return None


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
        images: tuple[ExtractionImageInput, ...] = (),
    ) -> ConversationReply:
        try:
            text, updated_session_summary = self._client.generate_reply(
                user_message=user_message,
                factual_context=factual_context,
                session_summary=session_summary,
                recent_turns=recent_turns,
                images=images,
            )
        except LLMConversationClientError as exc:
            logger.exception("Nutrition coach request failed: %s", exc)
            return ConversationReply(
                text=f"Не удалось получить ответ в режиме консультации. Причина: {exc}",
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

    def comment_on_food_write(
        self,
        *,
        saved_items: list[str],
        factual_context: NutritionCoachFactualContext,
        metric_deltas: dict[str, float],
    ) -> str | None:
        try:
            return self._client.generate_post_entry_comment(
                saved_items=saved_items,
                factual_context=factual_context,
                metric_deltas=metric_deltas,
            )
        except LLMConversationClientError as exc:
            logger.exception("Nutrition coach post-entry comment failed: %s", exc)
            return None
