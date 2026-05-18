from food_registry_bot.extraction import (
    InvalidExtractionPayload,
    StructuredPayloadExtractionService,
    ValidExtractionPayload,
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
