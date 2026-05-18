from types import SimpleNamespace

import pytest

from food_registry_bot.config import ExtractionProvider, Settings
from food_registry_bot.extraction import (
    InvalidExtractionPayload,
    LLMExtractionService,
    StructuredPayloadExtractionService,
    ValidExtractionPayload,
    create_extraction_service,
)


def test_structured_payload_service_returns_none_for_plain_text() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract_from_text("гречка с курицей")

    assert result is None


def test_structured_payload_service_parses_multiple_entries() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract_from_text(
        '{"entries": ['
        '{"type": "food", "items": [{"name": "гречка", "quantity": 200, "unit": "г"}]}, '
        '{"type": "water", "items": [{"name": "вода", "quantity": 250, "unit": "мл"}]}'
        "]}"
    )

    assert isinstance(result, ValidExtractionPayload)
    assert [entry.type.value for entry in result.payload.entries] == ["food", "water"]
    assert result.payload.entries[0].items[0].unit == "g"
    assert result.payload.entries[1].items[0].name == "water"
    assert result.payload.entries[1].items[0].unit == "ml"


def test_structured_payload_service_rejects_unit_without_quantity() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract_from_text(
        '{"entries": [{"type": "food", "items": [{"name": "гречка", "unit": "г"}]}]}'
    )

    assert isinstance(result, InvalidExtractionPayload)


def test_llm_extraction_service_validates_client_response() -> None:
    client = SimpleNamespace(
        extract_journal_payload=lambda _message_text: (
            '{"entries": [{"type": "food", "items": [{"name": "гречка"}]}]}'
        )
    )
    service = LLMExtractionService(client=client)

    result = service.extract_from_text("съел гречку")

    assert isinstance(result, ValidExtractionPayload)
    assert result.payload.entries[0].items[0].name == "гречка"


def test_llm_extraction_service_rejects_invalid_client_response() -> None:
    client = SimpleNamespace(extract_journal_payload=lambda _message_text: '{"entries": []}')
    service = LLMExtractionService(client=client)

    result = service.extract_from_text("съел гречку")

    assert isinstance(result, InvalidExtractionPayload)


def test_factory_uses_structured_payload_provider_by_default() -> None:
    settings = Settings()

    service = create_extraction_service(settings)

    assert isinstance(service, StructuredPayloadExtractionService)


def test_factory_requires_llm_client_for_llm_provider() -> None:
    settings = Settings.model_construct(
        extraction_provider=ExtractionProvider.LLM,
        llm_model="gpt-5-mini",
    )

    with pytest.raises(RuntimeError, match="EXTRACTION_PROVIDER=llm"):
        create_extraction_service(settings)


def test_factory_builds_llm_service_when_client_provided() -> None:
    settings = Settings.model_construct(
        extraction_provider=ExtractionProvider.LLM,
        llm_model="gpt-5-mini",
    )
    client = SimpleNamespace(
        extract_journal_payload=lambda _message_text: (
            '{"entries": [{"type": "water", "items": [{"name": "вода", "quantity": 250, "unit": "мл"}]}]}'
        )
    )

    service = create_extraction_service(settings, llm_client=client)

    assert isinstance(service, LLMExtractionService)
