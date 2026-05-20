from __future__ import annotations

from typing import Protocol

from food_registry_bot.conversation.context import (
    NutritionCoachConversationTurn,
    NutritionCoachFactualContext,
)


class LLMConversationClientError(RuntimeError):
    pass


class LLMConversationClient(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    @property
    def model_name(self) -> str:
        ...

    def generate_reply(
        self,
        *,
        user_message: str,
        factual_context: NutritionCoachFactualContext,
        session_summary: str | None,
        recent_turns: list[NutritionCoachConversationTurn],
    ) -> tuple[str, str | None]:
        """Return a plain-text conversational reply and updated session summary."""
