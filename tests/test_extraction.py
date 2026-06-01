from types import SimpleNamespace

import pytest

from food_registry_bot.config import ExtractionProvider, Settings
from food_registry_bot.extraction import (
    ExtractionImageInput,
    ExtractedJournalMetric,
    InvalidExtractionPayload,
    JournalExtractionRequest,
    LLMExtractionClientError,
    LLMExtractionService,
    OpenAIResponsesExtractionClient,
    StructuredPayloadExtractionService,
    ValidExtractionPayload,
    create_extraction_service,
)


def test_structured_payload_service_returns_none_for_plain_text() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(JournalExtractionRequest(text="гречка с курицей"))

    assert result is None


def test_structured_payload_service_parses_multiple_entries() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text=(
                '{"entries": ['
                '{"type": "food", "items": [{"name": "гречка", "quantity": 200, "unit": "г"}]}, '
                '{"type": "water", "items": [{"name": "вода", "quantity": 250, "unit": "мл"}]}'
                "]}"
            )
        )
    )

    assert isinstance(result, ValidExtractionPayload)
    assert [entry.type.value for entry in result.payload.entries] == ["food", "water"]
    assert result.extraction_provider == "structured_payload"
    assert result.extraction_model is None
    assert '"entries"' in result.raw_payload
    assert result.payload.entries[0].items[0].unit == "g"
    assert result.payload.entries[1].items[0].name == "water"
    assert result.payload.entries[1].items[0].unit == "ml"


def test_structured_payload_service_normalizes_water_alias_name_inside_water_entry() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text=(
                '{"entries": ['
                '{"type": "water", "items": [{"name": "стакан воды", "quantity": 250, "unit": "мл"}]}'
                "]}"
            )
        )
    )

    assert isinstance(result, ValidExtractionPayload)
    assert result.payload.entries[0].items[0].name == "water"
    assert result.payload.entries[0].items[0].unit == "ml"


def test_structured_payload_service_parses_food_nutrition_label() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text=(
                '{"entries": ['
                '{"type": "food", "items": [{"name": "батончик", "quantity": 7, "unit": "г", '
                '"nutrition_label": {"basis": "unknown", "calories": 480, "protein": 15, "fat": 37, "carbs": 22}}]}'
                "]}"
            )
        )
    )

    assert isinstance(result, ValidExtractionPayload)
    label = result.payload.entries[0].items[0].nutrition_label
    assert label is not None
    assert label.basis.value == "unknown"
    assert label.calories == 480


def test_structured_payload_service_rejects_unit_without_quantity() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text='{"entries": [{"type": "food", "items": [{"name": "гречка", "unit": "г"}]}]}'
        )
    )

    assert isinstance(result, InvalidExtractionPayload)


def test_structured_payload_service_rejects_quantity_without_unit() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text='{"entries": [{"type": "food", "items": [{"name": "гречка", "quantity": 200}]}]}'
        )
    )

    assert isinstance(result, InvalidExtractionPayload)


def test_structured_payload_service_rejects_non_base_food_unit() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text='{"entries": [{"type": "food", "items": [{"name": "яблоко", "quantity": 2, "unit": "pcs"}]}]}'
        )
    )

    assert isinstance(result, InvalidExtractionPayload)


def test_structured_payload_service_rejects_non_base_water_unit() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text='{"entries": [{"type": "water", "items": [{"name": "вода", "quantity": 1, "unit": "l"}]}]}'
        )
    )

    assert isinstance(result, InvalidExtractionPayload)


def test_structured_payload_service_allows_milliliters_for_liquid_food_item() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text='{"entries": [{"type": "food", "items": [{"name": "соус", "quantity": 25, "unit": "ml"}]}]}'
        )
    )

    assert isinstance(result, ValidExtractionPayload)
    assert result.payload.entries[0].items[0].unit == "ml"


def test_structured_payload_service_allows_workout_minutes() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text='{"entries": [{"type": "workout", "items": [{"name": "бег", "quantity": 40, "unit": "минут"}]}]}'
        )
    )

    assert isinstance(result, ValidExtractionPayload)
    assert result.payload.entries[0].type.value == "workout"
    assert result.payload.entries[0].items[0].unit == "min"


def test_structured_payload_service_allows_workout_calorie_metric() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text=(
                '{"entries": [{"type": "workout", "items": ['
                '{"name": "тренировка", "quantity": 90, "unit": "мин", '
                '"metrics": [{"code": "workout_calories", "value": 757, "confidence": "high"}]}]}]}'
            )
        )
    )

    assert isinstance(result, ValidExtractionPayload)
    metric = result.payload.entries[0].items[0].metrics[0]
    assert isinstance(metric, ExtractedJournalMetric)
    assert metric.code == "workout_calories"
    assert metric.value == 757


def test_structured_payload_service_rejects_non_workout_metric_on_workout_item() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text=(
                '{"entries": [{"type": "workout", "items": ['
                '{"name": "тренировка", "metrics": [{"code": "calories", "value": 757}]}]}]}'
            )
        )
    )

    assert isinstance(result, InvalidExtractionPayload)


def test_structured_payload_service_rejects_non_minute_workout_unit() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text='{"entries": [{"type": "workout", "items": [{"name": "бег", "quantity": 5, "unit": "km"}]}]}'
        )
    )

    assert isinstance(result, InvalidExtractionPayload)


def test_structured_payload_service_returns_none_for_photo_request() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            images=(ExtractionImageInput(data=b"image-bytes", media_type="image/jpeg"),)
        )
    )

    assert result is None


def test_llm_extraction_service_validates_client_response() -> None:
    client = SimpleNamespace(
        provider_name="openai_responses",
        model_name="gpt-5-mini",
        extract_journal_payload=lambda _request: (
            '{"entries": [{"type": "food", "items": [{"name": "гречка"}]}]}'
        )
    )
    service = LLMExtractionService(client=client)

    result = service.extract(JournalExtractionRequest(text="съел гречку"))

    assert isinstance(result, ValidExtractionPayload)
    assert result.extraction_provider == "openai_responses"
    assert result.extraction_model == "gpt-5-mini"
    assert result.raw_payload == '{"entries": [{"type": "food", "items": [{"name": "гречка"}]}]}'
    assert result.payload.entries[0].items[0].name == "гречка"


def test_llm_extraction_service_rejects_invalid_client_response() -> None:
    client = SimpleNamespace(
        provider_name="openai_responses",
        model_name="gpt-5-mini",
        extract_journal_payload=lambda _request: '{"entries": []}',
    )
    service = LLMExtractionService(client=client)

    result = service.extract(JournalExtractionRequest(text="съел гречку"))

    assert isinstance(result, InvalidExtractionPayload)


def test_llm_extraction_service_drops_food_metrics_before_validation() -> None:
    client = SimpleNamespace(
        provider_name="openai_responses",
        model_name="gpt-5-mini",
        extract_journal_payload=lambda _request: (
            '{"entries": [{"type": "food", "items": [{"name": "батончик", "quantity": 7, "unit": "g", '
            '"metrics": [{"code": "kcal", "value": 33.6, "confidence": "high"}]}]}]}'
        ),
    )
    service = LLMExtractionService(client=client)

    result = service.extract(JournalExtractionRequest(text="батончик 7 г"))

    assert isinstance(result, ValidExtractionPayload)
    assert result.payload.entries[0].items[0].metrics == []
    assert '"metrics"' not in result.raw_payload


def test_llm_extraction_service_drops_non_workout_calorie_metrics_before_validation() -> None:
    client = SimpleNamespace(
        provider_name="mistral_chat_completions",
        model_name="mistral-small-latest",
        extract_journal_payload=lambda _request: (
            '{"entries": [{"type": "workout", "items": [{"name": "тренировка", "quantity": 90, "unit": "min", '
            '"metrics": [{"code": "workout_calories", "value": 757, "confidence": "high"}, '
            '{"code": "avg_heart_rate", "value": 132, "confidence": "high"}]}]}]}'
        ),
    )
    service = LLMExtractionService(client=client)

    result = service.extract(JournalExtractionRequest(text="запиши тренировку"))

    assert isinstance(result, ValidExtractionPayload)
    metrics = result.payload.entries[0].items[0].metrics
    assert len(metrics) == 1
    assert metrics[0].code == "workout_calories"
    assert '"avg_heart_rate"' not in result.raw_payload


def test_llm_extraction_service_handles_client_errors() -> None:
    def raise_client_error(_request: JournalExtractionRequest) -> str:
        raise LLMExtractionClientError("boom")

    client = SimpleNamespace(
        provider_name="openai_responses",
        model_name="gpt-5-mini",
        extract_journal_payload=raise_client_error,
    )
    service = LLMExtractionService(client=client)

    result = service.extract(JournalExtractionRequest(text="съел гречку"))

    assert result == InvalidExtractionPayload(
        message="Не удалось получить structured payload от LLM.",
        provider="openai_responses",
        model="gpt-5-mini",
        technical_message="boom",
        error_code="client_error",
        is_llm=True,
    )


def test_factory_uses_structured_payload_provider_by_default() -> None:
    settings = Settings.model_construct(
        extraction_provider=ExtractionProvider.STRUCTURED_PAYLOAD,
        llm_model="gpt-5-mini",
    )

    service = create_extraction_service(settings)

    assert isinstance(service, StructuredPayloadExtractionService)


def test_factory_requires_api_key_for_llm_provider() -> None:
    settings = Settings.model_construct(
        extraction_provider=ExtractionProvider.LLM,
        llm_model="gpt-5-mini",
        openai_api_key=None,
    )

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        create_extraction_service(settings)


def test_factory_builds_llm_service_when_client_provided() -> None:
    settings = Settings.model_construct(
        extraction_provider=ExtractionProvider.LLM,
        llm_model="gpt-5-mini",
    )
    client = SimpleNamespace(
        provider_name="openai_responses",
        model_name="gpt-5-mini",
        extract_journal_payload=lambda _request: (
            '{"entries": [{"type": "water", "items": [{"name": "вода", "quantity": 250, "unit": "мл"}]}]}'
        )
    )

    service = create_extraction_service(settings, llm_client=client)

    assert isinstance(service, LLMExtractionService)


def test_openai_client_returns_output_text_from_sdk_response() -> None:
    sdk_client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(
                output_text='{"entries": [{"type": "food", "items": [{"name": "гречка"}]}]}'
            )
        )
    )
    client = OpenAIResponsesExtractionClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    assert client.provider_name == "openai_responses"
    assert client.model_name == "gpt-5-mini"
    result = client.extract_journal_payload(JournalExtractionRequest(text="съел гречку"))

    assert '"entries"' in result


def test_openai_client_raises_on_empty_sdk_output() -> None:
    sdk_client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(output_text=""))
    )
    client = OpenAIResponsesExtractionClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    with pytest.raises(LLMExtractionClientError, match="empty extraction response"):
        client.extract_journal_payload(JournalExtractionRequest(text="съел гречку"))


def test_openai_client_builds_multimodal_input() -> None:
    calls: list[dict] = []

    def create_response(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text='{"entries": [{"type": "food", "items": [{"name": "омлет"}]}]}')

    sdk_client = SimpleNamespace(responses=SimpleNamespace(create=create_response))
    client = OpenAIResponsesExtractionClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    client.extract_journal_payload(
        JournalExtractionRequest(
            text="омлет на фото",
            images=(ExtractionImageInput(data=b"image-bytes", media_type="image/jpeg"),),
        )
    )

    content = calls[0]["input"][0]["content"]
    assert calls[0]["instructions"]
    assert "Return item names in Russian" in calls[0]["instructions"]
    assert "For water entries, always set item.name to exactly 'water'" in calls[0]["instructions"]
    assert "If water quantity is present, use unit 'ml'" in calls[0]["instructions"]
    assert "Do not put nutrition metrics into food or water items" in calls[0]["instructions"]
    assert "Nutrition metrics for food are computed later by the nutrition layer" in calls[0]["instructions"]
    assert "item.nutrition_label" in calls[0]["instructions"]
    assert "basis='unknown'" in calls[0]["instructions"]
    assert "save it as one item and do not decompose it into guessed ingredients" in calls[0]["instructions"]
    assert "prefer grams for food and milliliters for water or drinks" in calls[0]["instructions"]
    assert "for liquid food items like dipping sauces, milliliters are also allowed" in calls[0]["instructions"]
    assert "Do not return a bare number without a unit" in calls[0]["instructions"]
    assert "estimate the weight of one piece first and then sum them" in calls[0]["instructions"]
    assert "If a dipping sauce is served separately" in calls[0]["instructions"]
    assert "round to a reasonable step such as 25 grams" in calls[0]["instructions"]
    assert content[0]["type"] == "input_text"
    assert "json" in content[0]["text"]
    assert content[1] == {"type": "input_text", "text": "омлет на фото"}
    assert content[2]["type"] == "input_image"
    assert content[2]["image_url"].startswith("data:image/jpeg;base64,")
