from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from food_registry_bot.db.models import Entry, EntryItem, EntryType
from food_registry_bot.config import NutritionProvider, Settings
from food_registry_bot.extraction import (
    ExtractedJournalEntry,
    ExtractedJournalItem,
    ExtractedJournalPayload,
    JournalExtractionRequest,
    StructuredPayloadExtractionService,
    ValidExtractionPayload,
)
from food_registry_bot.nutrition import (
    InvalidNutritionPayload,
    LLMNutritionEstimationService,
    LLMNutritionClientError,
    NutritionEstimationRequest,
    OpenAIResponsesNutritionClient,
    create_nutrition_service,
    prepare_nutrition_request_from_entries,
    prepare_nutrition_request_from_extracted_payload,
    resolve_nutrition_estimates,
    StaticNutritionEstimationService,
    ValidNutritionPayload,
)


def build_request() -> NutritionEstimationRequest:
    return NutritionEstimationRequest.model_validate(
        {
            "items": [
                {
                    "client_item_id": "entry-1:item-0",
                    "name": "гречка",
                    "quantity": 200,
                    "unit": "г",
                },
                {
                    "client_item_id": "entry-1:item-1",
                    "name": "курица",
                    "quantity": 150,
                    "unit": "g",
                },
            ]
        }
    )


def test_nutrition_request_normalizes_supported_units() -> None:
    request = build_request()

    assert request.items[0].unit.value == "g"
    assert request.items[1].unit.value == "g"


def test_nutrition_request_rejects_duplicate_client_item_ids() -> None:
    with pytest.raises(ValidationError, match="client_item_id values must be unique"):
        NutritionEstimationRequest.model_validate(
            {
                "items": [
                    {
                        "client_item_id": "entry-1:item-0",
                        "name": "гречка",
                        "quantity": 200,
                        "unit": "g",
                    },
                    {
                        "client_item_id": "entry-1:item-0",
                        "name": "курица",
                        "quantity": 150,
                        "unit": "g",
                    },
                ]
            }
        )


def test_static_nutrition_service_validates_payload() -> None:
    service = StaticNutritionEstimationService(
        raw_payload=(
            '{"items": ['
            '{"client_item_id": "entry-1:item-0", "calories": 220, "protein": 7.6, "fat": 2.2, "carbs": 42.8}, '
            '{"client_item_id": "entry-1:item-1", "calories": 248, "protein": 46.5, "fat": 5.4, "carbs": 0.0}'
            "]}"
        ),
        model_name="nutrition-stub-v1",
    )

    result = service.estimate(build_request())

    assert isinstance(result, ValidNutritionPayload)
    assert result.nutrition_provider == "static_nutrition_stub"
    assert result.nutrition_model == "nutrition-stub-v1"
    assert result.payload.items[0].calories == 220
    assert result.payload.items[1].protein == 46.5


def test_static_nutrition_service_rejects_missing_metrics() -> None:
    service = StaticNutritionEstimationService(
        raw_payload='{"items": [{"client_item_id": "entry-1:item-0", "calories": 220, "protein": 7.6, "fat": 2.2}]}'
    )

    result = service.estimate(build_request())

    assert isinstance(result, InvalidNutritionPayload)


def test_static_nutrition_service_rejects_payload_with_missing_requested_item() -> None:
    service = StaticNutritionEstimationService(
        raw_payload=(
            '{"items": ['
            '{"client_item_id": "entry-1:item-0", "calories": 220, "protein": 7.6, "fat": 2.2, "carbs": 42.8}'
            "]}"
        )
    )

    result = service.estimate(build_request())

    assert isinstance(result, InvalidNutritionPayload)


def test_llm_nutrition_service_validates_client_response() -> None:
    client = SimpleNamespace(
        provider_name="openai_responses",
        model_name="gpt-5-mini",
        estimate_nutrition_payload=lambda _request: (
            '{"items": ['
            '{"client_item_id": "entry-1:item-0", "calories": 220, "protein": 7.6, "fat": 2.2, "carbs": 42.8}, '
            '{"client_item_id": "entry-1:item-1", "calories": 248, "protein": 46.5, "fat": 5.4, "carbs": 0.0}'
            "]} "
        ),
    )
    service = LLMNutritionEstimationService(client=client)

    result = service.estimate(build_request())

    assert isinstance(result, ValidNutritionPayload)
    assert result.nutrition_provider == "openai_responses"
    assert result.nutrition_model == "gpt-5-mini"


def test_llm_nutrition_service_handles_client_errors() -> None:
    def raise_client_error(_request: NutritionEstimationRequest) -> str:
        raise LLMNutritionClientError("boom")

    client = SimpleNamespace(
        provider_name="openai_responses",
        model_name="gpt-5-mini",
        estimate_nutrition_payload=raise_client_error,
    )
    service = LLMNutritionEstimationService(client=client)

    result = service.estimate(build_request())

    assert result == InvalidNutritionPayload(
        message="Не удалось получить structured payload от nutrition provider."
    )


def test_factory_uses_static_provider_by_default() -> None:
    settings = Settings.model_construct(
        nutrition_provider=NutritionProvider.STATIC,
        nutrition_model="gpt-5-mini",
    )

    service = create_nutrition_service(settings, static_raw_payload='{"items": []}')

    assert isinstance(service, StaticNutritionEstimationService)


def test_factory_requires_api_key_for_llm_provider() -> None:
    settings = Settings.model_construct(
        nutrition_provider=NutritionProvider.LLM,
        nutrition_model="gpt-5-mini",
        openai_api_key=None,
    )

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        create_nutrition_service(settings)


def test_factory_builds_llm_service_when_client_provided() -> None:
    settings = Settings.model_construct(
        nutrition_provider=NutritionProvider.LLM,
        nutrition_model="gpt-5-mini",
    )
    client = SimpleNamespace(
        provider_name="openai_responses",
        model_name="gpt-5-mini",
        estimate_nutrition_payload=lambda _request: (
            '{"items": ['
            '{"client_item_id": "entry-1:item-0", "calories": 220, "protein": 7.6, "fat": 2.2, "carbs": 42.8}, '
            '{"client_item_id": "entry-1:item-1", "calories": 248, "protein": 46.5, "fat": 5.4, "carbs": 0.0}'
            "]}"
        ),
    )

    service = create_nutrition_service(settings, llm_client=client)

    assert isinstance(service, LLMNutritionEstimationService)


def test_openai_nutrition_client_returns_output_text_from_sdk_response() -> None:
    sdk_client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(
                output_text=(
                    '{"items": ['
                    '{"client_item_id": "entry-1:item-0", "calories": 220, "protein": 7.6, "fat": 2.2, "carbs": 42.8}, '
                    '{"client_item_id": "entry-1:item-1", "calories": 248, "protein": 46.5, "fat": 5.4, "carbs": 0.0}'
                    "]} "
                )
            )
        )
    )
    client = OpenAIResponsesNutritionClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    assert client.provider_name == "openai_responses"
    assert client.model_name == "gpt-5-mini"
    result = client.estimate_nutrition_payload(build_request())

    assert '"items"' in result


def test_openai_nutrition_client_raises_on_empty_sdk_output() -> None:
    sdk_client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(output_text=""))
    )
    client = OpenAIResponsesNutritionClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    with pytest.raises(LLMNutritionClientError, match="empty nutrition response"):
        client.estimate_nutrition_payload(build_request())


def test_openai_nutrition_client_builds_request_with_json_contract() -> None:
    calls: list[dict] = []

    def create_response(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            output_text=(
                '{"items": ['
                '{"client_item_id": "entry-1:item-0", "calories": 220, "protein": 7.6, "fat": 2.2, "carbs": 42.8}, '
                '{"client_item_id": "entry-1:item-1", "calories": 248, "protein": 46.5, "fat": 5.4, "carbs": 0.0}'
                "]} "
            )
        )

    sdk_client = SimpleNamespace(responses=SimpleNamespace(create=create_response))
    client = OpenAIResponsesNutritionClient(
        api_key="test-key",
        model="gpt-5-mini",
        client=sdk_client,
    )

    client.estimate_nutrition_payload(build_request())

    content = calls[0]["input"][0]["content"]
    assert calls[0]["instructions"]
    assert "Return only valid json matching this schema exactly" in calls[0]["instructions"]
    assert "same client_item_id as in the request" in calls[0]["instructions"]
    assert "Do not add explanations, confidence, ranges, or extra fields" in calls[0]["instructions"]
    assert content[0]["type"] == "input_text"
    assert content[1]["type"] == "input_text"
    assert '"client_item_id": "entry-1:item-0"' in content[1]["text"]


def test_prepare_nutrition_request_from_extracted_payload_filters_supported_food_items() -> None:
    payload = ExtractedJournalPayload(
        entries=[
            ExtractedJournalEntry(
                type=EntryType.FOOD,
                items=[
                    ExtractedJournalItem(name="гречка", quantity=200, unit="г"),
                    ExtractedJournalItem(name="омлет"),
                ],
            ),
            ExtractedJournalEntry(
                type=EntryType.WATER,
                items=[ExtractedJournalItem(name="вода", quantity=250, unit="мл")],
            ),
        ]
    )

    prepared_request = prepare_nutrition_request_from_extracted_payload(payload)

    assert prepared_request is not None
    assert [item.client_item_id for item in prepared_request.request.items] == ["entry-0:item-0"]
    assert prepared_request.request.items[0].name == "гречка"
    assert prepared_request.request.items[0].quantity == 200
    assert prepared_request.request.items[0].unit.value == "g"


def test_prepare_nutrition_request_from_extracted_payload_returns_none_without_supported_items() -> None:
    payload = ExtractedJournalPayload(
        entries=[
            ExtractedJournalEntry(
                type=EntryType.FOOD,
                items=[ExtractedJournalItem(name="омлет")],
            ),
            ExtractedJournalEntry(
                type=EntryType.WATER,
                items=[ExtractedJournalItem(name="вода", quantity=250, unit="мл")],
            ),
        ]
    )

    assert prepare_nutrition_request_from_extracted_payload(payload) is None


def test_prepare_nutrition_request_from_entries_filters_supported_food_items() -> None:
    entry = Entry(id=42, user_id=1, entry_type=EntryType.FOOD, occurred_at="2026-05-18T10:00:00Z")
    entry.items = [
        EntryItem(position=1, name="соус", quantity=30, unit="ml"),
        EntryItem(position=0, name="курица", quantity=150, unit="g"),
        EntryItem(position=2, name="омлет", quantity=None, unit=None),
    ]
    water_entry = Entry(id=43, user_id=1, entry_type=EntryType.WATER, occurred_at="2026-05-18T10:05:00Z")
    water_entry.items = [EntryItem(position=0, name="water", quantity=250, unit="ml")]

    prepared_request = prepare_nutrition_request_from_entries([entry, water_entry])

    assert prepared_request is not None
    assert [item.client_item_id for item in prepared_request.request.items] == [
        "entry-42:item-0",
        "entry-42:item-1",
    ]
    assert [item.name for item in prepared_request.request.items] == ["курица", "соус"]


def test_resolve_nutrition_estimates_returns_results_in_request_order() -> None:
    prepared_request = prepare_nutrition_request_from_extracted_payload(
        ExtractedJournalPayload(
            entries=[
                ExtractedJournalEntry(
                    type=EntryType.FOOD,
                    items=[
                        ExtractedJournalItem(name="гречка", quantity=200, unit="г"),
                        ExtractedJournalItem(name="курица", quantity=150, unit="г"),
                    ],
                )
            ]
        )
    )
    assert prepared_request is not None

    service = StaticNutritionEstimationService(
        raw_payload=(
            '{"items": ['
            '{"client_item_id": "entry-0:item-1", "calories": 248, "protein": 46.5, "fat": 5.4, "carbs": 0.0}, '
            '{"client_item_id": "entry-0:item-0", "calories": 220, "protein": 7.6, "fat": 2.2, "carbs": 42.8}'
            "]}"
        )
    )
    result = service.estimate(prepared_request.request)
    assert isinstance(result, ValidNutritionPayload)

    estimates = resolve_nutrition_estimates(prepared_request, result.payload)

    assert [estimate.item_ref.client_item_id for estimate in estimates] == [
        "entry-0:item-0",
        "entry-0:item-1",
    ]
    assert estimates[0].calories == 220
    assert estimates[1].protein == 46.5


def test_extraction_to_nutrition_pipeline_on_structured_payload() -> None:
    extraction_service = StructuredPayloadExtractionService()
    extraction_result = extraction_service.extract(
        JournalExtractionRequest(
            text=(
                '{"entries": ['
                '{"type": "food", "items": [{"name": "гречка", "quantity": 200, "unit": "г"}]}, '
                '{"type": "water", "items": [{"name": "вода", "quantity": 250, "unit": "мл"}]}, '
                '{"type": "food", "items": [{"name": "курица", "quantity": 150, "unit": "г"}]}'
                "]}"
            ),
        )
    )
    assert isinstance(extraction_result, ValidExtractionPayload)

    prepared_request = prepare_nutrition_request_from_extracted_payload(extraction_result.payload)
    assert prepared_request is not None
    assert [item.client_item_id for item in prepared_request.request.items] == [
        "entry-0:item-0",
        "entry-2:item-0",
    ]

    nutrition_service = StaticNutritionEstimationService(
        raw_payload=(
            '{"items": ['
            '{"client_item_id": "entry-0:item-0", "calories": 220, "protein": 7.6, "fat": 2.2, "carbs": 42.8}, '
            '{"client_item_id": "entry-2:item-0", "calories": 248, "protein": 46.5, "fat": 5.4, "carbs": 0.0}'
            "]}"
        )
    )
    nutrition_result = nutrition_service.estimate(prepared_request.request)

    assert isinstance(nutrition_result, ValidNutritionPayload)
    estimates = resolve_nutrition_estimates(prepared_request, nutrition_result.payload)

    assert [estimate.item_ref.name for estimate in estimates] == ["гречка", "курица"]
    assert [estimate.calories for estimate in estimates] == [220, 248]
