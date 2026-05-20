from types import SimpleNamespace

from food_registry_bot.config import Settings
from food_registry_bot.conversation.context import NutritionCoachFactualContext
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
        generate_reply=lambda *, user_message, factual_context: f"reply:{user_message}:{factual_context.summary_date}",
    )

    service = create_conversation_service(settings, llm_client=client)

    assert isinstance(service, LLMConversationService)
    reply = service.reply(
        user_message="привет",
        factual_context=NutritionCoachFactualContext(
            summary_date="2026-05-20",
            timezone="Europe/Moscow",
            nutrition_day_start_hour=4,
            day_totals={"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0, "fiber": 0.0, "water": 0.0},
            goal_progress={},
            recent_entries=[],
            nutrition_summary_is_complete=True,
            excluded_food_entry_count=0,
            water_summary_is_complete=True,
            excluded_water_entry_count=0,
        ),
    )
    assert reply.text == "reply:привет:2026-05-20"
    assert reply.provider == "test_provider"
    assert reply.model == "test-model"
