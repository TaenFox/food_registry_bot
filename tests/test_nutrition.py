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
    LLMNutritionClientError,
    LLMNutritionEstimationService,
    NutritionConfidence,
    NutritionEstimationRequest,
    OpenAIResponsesNutritionClient,
    StaticNutritionEstimationService,
    ValidNutritionPayload,
    create_nutrition_service,
    prepare_nutrition_request_from_entries,
    prepare_nutrition_request_from_extracted_payload,
    resolve_nutrition_estimates,
)
from food_registry_bot.diet.mistral_client import MistralChatCompletionsDietClient
from food_registry_bot.diet.openai_client import OpenAIResponsesDietClient


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
                    "name": "омлет",
                },
            ]
        }
    )


def build_metric_payload(item_ids: list[str], *, confidence: str = "medium") -> str:
    items = []
    for index, item_id in enumerate(item_ids):
        items.append(
            {
                "client_item_id": item_id,
                "metrics": [
                    {"code": "calories", "value": 220.0 + index, "confidence": confidence},
                    {"code": "protein", "value": 7.6 + index, "confidence": confidence},
                    {"code": "fat", "value": 2.2 + index, "confidence": confidence},
                    {"code": "carbs", "value": 42.8 + index, "confidence": confidence},
                    {"code": "fiber", "value": 5.1 + index, "confidence": confidence},
                ],
            }
        )
    import json

    return json.dumps({"items": items}, ensure_ascii=False)


def test_nutrition_request_normalizes_supported_units() -> None:
    request = build_request()

    assert request.items[0].unit.value == "g"
    assert request.items[1].quantity is None
    assert request.items[1].unit is None


def test_nutrition_request_rejects_duplicate_client_item_ids() -> None:
    with pytest.raises(ValidationError, match="client_item_id values must be unique"):
        NutritionEstimationRequest.model_validate(
            {
                "items": [
                    {
                        "client_item_id": "entry-1:item-0",
                        "name": "гречка",
                    },
                    {
                        "client_item_id": "entry-1:item-0",
                        "name": "курица",
                    },
                ]
            }
        )


def test_nutrition_request_rejects_quantity_without_unit() -> None:
    with pytest.raises(ValidationError, match="quantity requires unit"):
        NutritionEstimationRequest.model_validate(
            {
                "items": [
                    {
                        "client_item_id": "entry-1:item-0",
                        "name": "гречка",
                        "quantity": 200,
                    }
                ]
            }
        )


def test_nutrition_request_accepts_structured_nutrition_label() -> None:
    request = NutritionEstimationRequest.model_validate(
        {
            "items": [
                {
                    "client_item_id": "entry-1:item-0",
                    "name": "батончик",
                    "quantity": 7,
                    "unit": "г",
                    "nutrition_label": {
                        "basis": "unknown",
                        "calories": 480,
                        "protein": 15,
                        "fat": 37,
                        "carbs": 22,
                    },
                }
            ]
        }
    )

    assert request.items[0].nutrition_label is not None
    assert request.items[0].nutrition_label.basis.value == "unknown"
    assert request.items[0].nutrition_label.calories == 480


def test_static_nutrition_service_validates_payload() -> None:
    service = StaticNutritionEstimationService(
        raw_payload=build_metric_payload(["entry-1:item-0", "entry-1:item-1"]),
        model_name="nutrition-stub-v1",
    )

    result = service.estimate(build_request())

    assert isinstance(result, ValidNutritionPayload)
    assert result.nutrition_provider == "static_nutrition_stub"
    assert result.nutrition_model == "nutrition-stub-v1"
    assert result.payload.items[0].metrics[0].code == "calories"
    assert result.payload.items[1].metrics[1].value == 8.6


def test_static_nutrition_service_rejects_missing_metrics() -> None:
    service = StaticNutritionEstimationService(
        raw_payload=(
            '{"items": [{"client_item_id": "entry-1:item-0", "metrics": ['
            '{"code": "calories", "value": 220, "confidence": "medium"}'
            "]}]}"
        )
    )

    result = service.estimate(build_request())

    assert isinstance(result, InvalidNutritionPayload)


def test_static_nutrition_service_rejects_payload_with_missing_requested_item() -> None:
    service = StaticNutritionEstimationService(
        raw_payload=build_metric_payload(["entry-1:item-0"])
    )

    result = service.estimate(build_request())

    assert isinstance(result, InvalidNutritionPayload)


def test_llm_nutrition_service_validates_client_response() -> None:
    client = SimpleNamespace(
        provider_name="openai_responses",
        model_name="gpt-5-mini",
        estimate_nutrition_payload=lambda _request: build_metric_payload(
            ["entry-1:item-0", "entry-1:item-1"]
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
        message="Не удалось получить structured payload от nutrition provider.",
        provider="openai_responses",
        model="gpt-5-mini",
        technical_message="boom",
        error_code="client_error",
        is_llm=True,
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
        estimate_nutrition_payload=lambda _request: build_metric_payload(
            ["entry-1:item-0", "entry-1:item-1"]
        ),
    )

    service = create_nutrition_service(settings, llm_client=client)

    assert isinstance(service, LLMNutritionEstimationService)


def test_openai_nutrition_client_returns_output_text_from_sdk_response() -> None:
    sdk_client = SimpleNamespace(
        responses=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(
                output_text=build_metric_payload(["entry-1:item-0", "entry-1:item-1"])
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
        return SimpleNamespace(output_text=build_metric_payload(["entry-1:item-0", "entry-1:item-1"]))

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
    assert "Return confidence for every metric using only low, medium, or high" in calls[0]["instructions"]
    assert "If nutrition_label is present for an item" in calls[0]["instructions"]
    assert "basis is unknown" in calls[0]["instructions"]
    assert content[0]["type"] == "input_text"
    assert content[1]["type"] == "input_text"
    assert '"client_item_id": "entry-1:item-0"' in content[1]["text"]


def test_diet_prompts_require_evidence_based_guidance() -> None:
    openai_prompt = OpenAIResponsesDietClient._build_system_prompt()
    mistral_prompt = MistralChatCompletionsDietClient._build_system_prompt()

    assert "evidence-based diet guidance" in openai_prompt
    assert "not fads" in openai_prompt
    assert "evidence-based diet guidance" in mistral_prompt
    assert "not fads" in mistral_prompt


def test_prepare_nutrition_request_from_extracted_payload_includes_food_items_without_quantity() -> None:
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
    assert [item.client_item_id for item in prepared_request.request.items] == [
        "entry-0:item-0",
        "entry-0:item-1",
    ]
    assert prepared_request.request.items[1].quantity is None


def test_prepare_nutrition_request_from_entries_includes_food_items_without_quantity() -> None:
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
        "entry-42:item-2",
    ]


def test_prepare_nutrition_request_from_entries_restores_nutrition_label_from_extraction_trace() -> None:
    entry = Entry(
        id=44,
        user_id=1,
        entry_type=EntryType.FOOD,
        occurred_at="2026-05-18T10:00:00Z",
        extraction_raw_payload=(
            '{"entries": [{"type": "food", "items": [{"name": "батончик", "quantity": 7, "unit": "g", '
            '"nutrition_label": {"basis": "unknown", "calories": 480, "protein": 15, "fat": 37, "carbs": 22}}]}]}'
        ),
    )
    entry.items = [EntryItem(position=0, name="батончик", quantity=7, unit="g")]

    prepared_request = prepare_nutrition_request_from_entries([entry])

    assert prepared_request is not None
    label = prepared_request.request.items[0].nutrition_label
    assert label is not None
    assert label.calories == 480
    assert label.basis.value == "unknown"


def test_prepare_nutrition_request_from_entries_drops_unsupported_unit() -> None:
    entry = Entry(id=52, user_id=1, entry_type=EntryType.FOOD, occurred_at="2026-05-18T10:00:00Z")
    entry.items = [EntryItem(position=0, name="яйцо", quantity=2, unit="шт")]

    prepared_request = prepare_nutrition_request_from_entries([entry])

    assert prepared_request is not None
    assert prepared_request.request.items[0].quantity is None
    assert prepared_request.request.items[0].unit is None


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
        raw_payload=build_metric_payload(["entry-0:item-1", "entry-0:item-0"])
    )
    result = service.estimate(prepared_request.request)
    assert isinstance(result, ValidNutritionPayload)

    estimates = resolve_nutrition_estimates(prepared_request, result.payload)

    assert [estimate.item_ref.client_item_id for estimate in estimates] == [
        "entry-0:item-0",
        "entry-0:item-1",
    ]
    assert estimates[0].metrics[0].code == "calories"
    assert estimates[0].metrics[0].confidence is NutritionConfidence.MEDIUM


def test_extraction_to_nutrition_pipeline_on_structured_payload() -> None:
    extraction_service = StructuredPayloadExtractionService()
    extraction_result = extraction_service.extract(
        JournalExtractionRequest(
            text=(
                '{"entries": ['
                '{"type": "food", "items": [{"name": "гречка", "quantity": 200, "unit": "г"}]}, '
                '{"type": "water", "items": [{"name": "вода", "quantity": 250, "unit": "мл"}]}, '
                '{"type": "food", "items": [{"name": "курица"}]}'
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
        raw_payload=build_metric_payload(["entry-0:item-0", "entry-2:item-0"], confidence="low")
    )
    nutrition_result = nutrition_service.estimate(prepared_request.request)

    assert isinstance(nutrition_result, ValidNutritionPayload)
    estimates = resolve_nutrition_estimates(prepared_request, nutrition_result.payload)

    assert [estimate.item_ref.name for estimate in estimates] == ["гречка", "курица"]
    assert all(metric.confidence is NutritionConfidence.LOW for metric in estimates[1].metrics)


def test_extraction_to_nutrition_pipeline_keeps_structured_nutrition_label() -> None:
    extraction_service = StructuredPayloadExtractionService()
    extraction_result = extraction_service.extract(
        JournalExtractionRequest(
            text=(
                '{"entries": ['
                '{"type": "food", "items": [{"name": "батончик", "quantity": 7, "unit": "г", '
                '"nutrition_label": {"basis": "unknown", "calories": 480, "protein": 15, "fat": 37, "carbs": 22}}]}'
                "]}"
            ),
        )
    )
    assert isinstance(extraction_result, ValidExtractionPayload)

    prepared_request = prepare_nutrition_request_from_extracted_payload(extraction_result.payload)
    assert prepared_request is not None
    label = prepared_request.request.items[0].nutrition_label
    assert label is not None
    assert label.calories == 480
