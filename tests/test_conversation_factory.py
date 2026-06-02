from datetime import datetime, timezone
from types import SimpleNamespace

from food_registry_bot.config import Settings
from food_registry_bot.conversation.context import NutritionCoachConversationTurn, NutritionCoachFactualContext
from food_registry_bot.conversation.factory import create_conversation_service
from food_registry_bot.conversation.llm_client import LLMConversationClientError
from food_registry_bot.conversation.mistral_client import MistralChatCompletionsConversationClient
from food_registry_bot.conversation.openai_client import OpenAIResponsesConversationClient
from food_registry_bot.conversation.service import DisabledConversationService, LLMConversationService
from food_registry_bot.extraction.request import ExtractionImageInput


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
        generate_reply=lambda **kwargs: (
            f"reply:{kwargs['user_message']}:{kwargs['factual_context'].summary_date}",
            "updated-summary",
        ),
        generate_post_entry_comment=lambda **kwargs: (
            f"comment:{kwargs['saved_items'][0]}:{kwargs['factual_context'].summary_date}"
        ),
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
        session_summary="старый summary",
        recent_turns=[
            NutritionCoachConversationTurn(
                role="user",
                content="предыдущий вопрос",
                created_at=datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc),
            )
        ],
    )
    assert reply.text == "reply:привет:2026-05-20"
    assert reply.provider == "test_provider"
    assert reply.model == "test-model"
    assert reply.updated_session_summary == "updated-summary"
    comment = service.comment_on_food_write(
        saved_items=["яблоко: 180 г"],
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
        metric_deltas={"calories": 220.0},
    )
    assert comment == "comment:яблоко: 180 г:2026-05-20"


def test_openai_conversation_client_falls_back_to_plain_text_reply_when_json_is_invalid() -> None:
    sdk_client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(
                output_text="Если до тренировки 10 минут, лучше банан или немного сока."
            )
        )
    )
    client = OpenAIResponsesConversationClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    reply_text, updated_summary = client.generate_reply(
        user_message="а если через 10 минут?",
        factual_context=NutritionCoachFactualContext(
            summary_date="2026-05-20",
            timezone="Europe/Moscow",
            nutrition_day_start_hour=4,
            day_totals={"calories": 1012.0, "protein": 52.0, "fat": 40.1, "carbs": 107.9, "fiber": 21.7, "water": 750.0},
            goal_progress={},
            recent_entries=[],
            nutrition_summary_is_complete=True,
            excluded_food_entry_count=0,
            water_summary_is_complete=True,
            excluded_water_entry_count=0,
        ),
        session_summary="говорили про предтренировочный перекус",
        recent_turns=[],
    )

    assert reply_text == "Если до тренировки 10 минут, лучше банан или немного сока."
    assert updated_summary == "говорили про предтренировочный перекус"


def test_openai_conversation_client_keeps_previous_summary_when_llm_returns_empty_summary() -> None:
    sdk_client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(
                output_text='{"reply_text":"Бери быстрые углеводы и немного воды.","updated_session_summary":""}'
            )
        )
    )
    client = OpenAIResponsesConversationClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    reply_text, updated_summary = client.generate_reply(
        user_message="а если через 10 минут?",
        factual_context=NutritionCoachFactualContext(
            summary_date="2026-05-20",
            timezone="Europe/Moscow",
            nutrition_day_start_hour=4,
            day_totals={"calories": 1012.0, "protein": 52.0, "fat": 40.1, "carbs": 107.9, "fiber": 21.7, "water": 750.0},
            goal_progress={},
            recent_entries=[],
            nutrition_summary_is_complete=True,
            excluded_food_entry_count=0,
            water_summary_is_complete=True,
            excluded_water_entry_count=0,
        ),
        session_summary="говорили про предтренировочный перекус",
        recent_turns=[],
    )

    assert reply_text == "Бери быстрые углеводы и немного воды."
    assert updated_summary == "говорили про предтренировочный перекус"


def test_openai_conversation_client_serializes_recent_turns_from_dataclass_objects() -> None:
    captured_kwargs = {}

    def create_stub(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            output_text='{"reply_text":"Ок, уточняю ответ.","updated_session_summary":"новый summary"}'
        )

    sdk_client = SimpleNamespace(
        responses=SimpleNamespace(create=create_stub)
    )
    client = OpenAIResponsesConversationClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    reply_text, updated_summary = client.generate_reply(
        user_message="а если через 10 минут?",
        factual_context=NutritionCoachFactualContext(
            summary_date="2026-05-20",
            timezone="Europe/Moscow",
            nutrition_day_start_hour=4,
            day_totals={"calories": 1012.0, "protein": 52.0, "fat": 40.1, "carbs": 107.9, "fiber": 21.7, "water": 750.0},
            goal_progress={},
            recent_entries=[],
            nutrition_summary_is_complete=True,
            excluded_food_entry_count=0,
            water_summary_is_complete=True,
            excluded_water_entry_count=0,
        ),
        session_summary="говорили про предтренировочный перекус",
        recent_turns=[
            NutritionCoachConversationTurn(
                role="user",
                content="что лучше съесть перед вечерней тренировкой?",
                created_at=datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc),
            ),
            NutritionCoachConversationTurn(
                role="assistant",
                content="дам варианты",
                created_at=datetime(2026, 5, 20, 10, 1, tzinfo=timezone.utc),
            ),
        ],
    )

    serialized_payload = captured_kwargs["input"][0]["content"][2]["text"]
    assert '"recent_turns"' in serialized_payload
    assert '"role": "user"' in serialized_payload
    assert '"content": "что лучше съесть перед вечерней тренировкой?"' in serialized_payload
    assert reply_text == "Ок, уточняю ответ."
    assert updated_summary == "новый summary"


def test_openai_conversation_client_serializes_workout_entries_from_factual_context() -> None:
    captured_kwargs = {}

    def create_stub(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            output_text='{"reply_text":"Учитываю тренировку дня.","updated_session_summary":"обсудили питание после нагрузки"}'
        )

    sdk_client = SimpleNamespace(responses=SimpleNamespace(create=create_stub))
    client = OpenAIResponsesConversationClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    reply_text, updated_summary = client.generate_reply(
        user_message="что съесть на ужин?",
        factual_context=NutritionCoachFactualContext(
            summary_date="2026-05-20",
            timezone="Europe/Moscow",
            nutrition_day_start_hour=4,
            day_totals={"calories": 1012.0, "protein": 52.0, "fat": 40.1, "carbs": 107.9, "fiber": 21.7, "water": 750.0},
            goal_progress={},
            workout_entries=[
                {
                    "entry_id": 10,
                    "occurred_at": datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
                    "source_text": "сегодня была пробежка 40 минут",
                    "items": [
                        {"name": "бег", "quantity": 40, "unit": "min", "rendered_value": "бег: 40 мин"}
                    ],
                }
            ],
            recent_entries=[],
            nutrition_summary_is_complete=True,
            excluded_food_entry_count=0,
            water_summary_is_complete=True,
            excluded_water_entry_count=0,
        ),
        session_summary="говорили про ужин",
        recent_turns=[],
    )

    serialized_payload = captured_kwargs["input"][0]["content"][2]["text"]
    assert '"workout_entries"' in serialized_payload
    assert '"source_text": "сегодня была пробежка 40 минут"' in serialized_payload
    assert '"rendered_value": "бег: 40 мин"' in serialized_payload
    assert reply_text == "Учитываю тренировку дня."
    assert updated_summary == "обсудили питание после нагрузки"


def test_openai_conversation_client_serializes_input_images_for_multimodal_coaching() -> None:
    captured_kwargs = {}

    def create_stub(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(
            output_text='{"reply_text":"Из этого можно сделать овощной ужин.","updated_session_summary":"обсуждали фото продуктов"}'
        )

    sdk_client = SimpleNamespace(
        responses=SimpleNamespace(create=create_stub)
    )
    client = OpenAIResponsesConversationClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    reply_text, updated_summary = client.generate_reply(
        user_message="что лучше приготовить из этого?",
        factual_context=NutritionCoachFactualContext(
            summary_date="2026-05-20",
            timezone="Europe/Moscow",
            nutrition_day_start_hour=4,
            day_totals={"calories": 1012.0, "protein": 52.0, "fat": 40.1, "carbs": 107.9, "fiber": 21.7, "water": 750.0},
            goal_progress={},
            recent_entries=[],
            nutrition_summary_is_complete=True,
            excluded_food_entry_count=0,
            water_summary_is_complete=True,
            excluded_water_entry_count=0,
        ),
        session_summary=None,
        recent_turns=[],
        images=(ExtractionImageInput(data=b"image-bytes", media_type="image/jpeg"),),
    )

    content = captured_kwargs["input"][0]["content"]
    image_part = content[-1]
    assert image_part["type"] == "input_image"
    assert image_part["image_url"].startswith("data:image/jpeg;base64,")
    assert reply_text == "Из этого можно сделать овощной ужин."
    assert updated_summary == "обсуждали фото продуктов"


def test_openai_conversation_client_returns_post_entry_comment_from_json_payload() -> None:
    sdk_client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(
                output_text='{"comment_text":"После этой записи белок немного вырос, но до цели по белку ещё заметный запас."}'
            )
        )
    )
    client = OpenAIResponsesConversationClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    comment = client.generate_post_entry_comment(
        saved_items=["яблоко: 180 г"],
        factual_context=NutritionCoachFactualContext(
            summary_date="2026-05-20",
            timezone="Europe/Moscow",
            nutrition_day_start_hour=4,
            day_totals={"calories": 220.0, "protein": 7.6, "fat": 2.2, "carbs": 42.8, "fiber": 5.1, "water": 0.0},
            goal_progress={},
            recent_entries=[],
            nutrition_summary_is_complete=True,
            excluded_food_entry_count=0,
            water_summary_is_complete=True,
            excluded_water_entry_count=0,
        ),
        metric_deltas={"calories": 220.0, "protein": 7.6},
    )

    assert comment == "После этой записи белок немного вырос, но до цели по белку ещё заметный запас."


def test_mistral_conversation_client_requests_json_schema_response_format() -> None:
    captured_kwargs = {}

    class StubClient:
        def complete(self, **kwargs):
            captured_kwargs.update(kwargs)
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"reply_text":"Нормальный ответ.","updated_session_summary":"summary"}'
                        }
                    }
                ]
            }

    client = MistralChatCompletionsConversationClient(
        api_key="test-key",
        model="mistral-small-latest",
        client=StubClient(),
    )

    reply_text, updated_summary = client.generate_reply(
        user_message="что съесть на ужин?",
        factual_context=NutritionCoachFactualContext(
            summary_date="2026-05-20",
            timezone="Europe/Moscow",
            nutrition_day_start_hour=4,
            day_totals={"calories": 1012.0, "protein": 52.0, "fat": 40.1, "carbs": 107.9, "fiber": 21.7, "water": 750.0},
            goal_progress={},
            recent_entries=[],
            nutrition_summary_is_complete=True,
            excluded_food_entry_count=0,
            water_summary_is_complete=True,
            excluded_water_entry_count=0,
        ),
        session_summary="говорили про ужин",
        recent_turns=[],
    )

    assert reply_text == "Нормальный ответ."
    assert updated_summary == "summary"
    assert captured_kwargs["response_format"]["type"] == "json_schema"
    assert captured_kwargs["response_format"]["json_schema"]["name"] == "nutrition_coach_reply"


def test_mistral_conversation_client_rejects_schema_echo_payload() -> None:
    class StubClient:
        def complete(self, **_kwargs):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"additionalProperties": false, "properties": {"reply_text": {"type": "string"}}, '
                                '"required": ["reply_text"], "title": "NutritionCoachLLMReply"}'
                            )
                        }
                    }
                ]
            }

    client = MistralChatCompletionsConversationClient(
        api_key="test-key",
        model="mistral-small-latest",
        client=StubClient(),
    )

    try:
        client.generate_reply(
            user_message="покажи топ 10 круп по клетчатке",
            factual_context=NutritionCoachFactualContext(
                summary_date="2026-05-20",
                timezone="Europe/Moscow",
                nutrition_day_start_hour=4,
                day_totals={"calories": 1012.0, "protein": 52.0, "fat": 40.1, "carbs": 107.9, "fiber": 21.7, "water": 750.0},
                goal_progress={},
                recent_entries=[],
                nutrition_summary_is_complete=True,
                excluded_food_entry_count=0,
                water_summary_is_complete=True,
                excluded_water_entry_count=0,
            ),
            session_summary="говорили про крупы",
            recent_turns=[],
        )
    except LLMConversationClientError as exc:
        assert "invalid coach response payload" in str(exc)
    else:
        raise AssertionError("Expected schema echo payload to raise LLMConversationClientError")


def test_llm_conversation_service_swallows_post_entry_comment_error() -> None:
    settings = Settings.model_construct(
        openai_api_key=None,
        conversation_model="gpt-5-mini",
    )
    client = SimpleNamespace(
        provider_name="test_provider",
        model_name="test-model",
        generate_reply=lambda **_kwargs: ("ok", "summary"),
        generate_post_entry_comment=lambda **_kwargs: (_ for _ in ()).throw(
            LLMConversationClientError("comment failure")
        ),
    )

    service = create_conversation_service(settings, llm_client=client)

    comment = service.comment_on_food_write(
        saved_items=["яблоко: 180 г"],
        factual_context=NutritionCoachFactualContext(
            summary_date="2026-05-20",
            timezone="Europe/Moscow",
            nutrition_day_start_hour=4,
            day_totals={"calories": 220.0, "protein": 7.6, "fat": 2.2, "carbs": 42.8, "fiber": 5.1, "water": 0.0},
            goal_progress={},
            recent_entries=[],
            nutrition_summary_is_complete=True,
            excluded_food_entry_count=0,
            water_summary_is_complete=True,
            excluded_water_entry_count=0,
        ),
        metric_deltas={"calories": 220.0, "protein": 7.6},
    )

    assert comment is None


def test_llm_conversation_service_includes_error_reason_in_fallback_reply() -> None:
    settings = Settings.model_construct(
        openai_api_key=None,
        conversation_model="gpt-5-mini",
    )
    client = SimpleNamespace(
        provider_name="test_provider",
        model_name="test-model",
        generate_reply=lambda **_kwargs: (_ for _ in ()).throw(LLMConversationClientError("test failure")),
    )

    service = create_conversation_service(settings, llm_client=client)

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
        session_summary="старый summary",
        recent_turns=[],
    )

    assert reply.text == "Не получилось ответить в режиме консультации. Попробуй задать вопрос короче или повторить позже."
    assert reply.updated_session_summary == "старый summary"
