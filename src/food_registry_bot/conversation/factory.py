from __future__ import annotations

from food_registry_bot.config import Settings
from food_registry_bot.conversation.llm_client import LLMConversationClient
from food_registry_bot.conversation.openai_client import OpenAIResponsesConversationClient
from food_registry_bot.conversation.service import (
    ConversationService,
    DisabledConversationService,
    LLMConversationService,
)


def create_conversation_service(
    settings: Settings,
    *,
    llm_client: LLMConversationClient | None = None,
) -> ConversationService:
    if not settings.openai_api_key and llm_client is None:
        return DisabledConversationService()

    if llm_client is None:
        llm_client = OpenAIResponsesConversationClient(
            api_key=settings.openai_api_key,
            model=settings.conversation_model,
        )

    return LLMConversationService(client=llm_client)
