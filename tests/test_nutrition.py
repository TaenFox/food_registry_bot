from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from food_registry_bot.nutrition import (
    InvalidNutritionPayload,
    LLMNutritionEstimationService,
    NutritionEstimationRequest,
    NutritionPayloadClientError,
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
        raise NutritionPayloadClientError("boom")

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
