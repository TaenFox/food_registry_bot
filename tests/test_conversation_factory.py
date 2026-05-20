from types import SimpleNamespace

from food_registry_bot.config import Settings
from food_registry_bot.conversation.factory import create_conversation_service
from food_registry_bot.conversation.service import DisabledConversationService, LLMConversationService


def test_create_conversation_service_returns_disabled_without_openai_key() -> None:
    settings = Settings.model_construct(
        openai_api_key=None,
        conversation_model="gpt-5-mini",
    )

    service = create_conversation_service(settings)

    assert isinstance(service, DisabledConversationService)


def test_create_conversation_service_uses_injected_llm_client() -> None:
    settings = Settings.model_construct(
        openai_api_key=None,
        conversation_model="gpt-5-mini",
    )
    client = SimpleNamespace(
        provider_name="test_provider",
        model_name="test-model",
        generate_reply=lambda *, user_message: f"reply:{user_message}",
    )

    service = create_conversation_service(settings, llm_client=client)

    assert isinstance(service, LLMConversationService)
    reply = service.reply(user_message="привет")
    assert reply.text == "reply:привет"
    assert reply.provider == "test_provider"
    assert reply.model == "test-model"
