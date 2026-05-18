from types import SimpleNamespace

import pytest

from food_registry_bot.config import ExtractionProvider, Settings
from food_registry_bot.extraction import (
    ExtractionImageInput,
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


def test_structured_payload_service_rejects_unit_without_quantity() -> None:
    service = StructuredPayloadExtractionService()

    result = service.extract(
        JournalExtractionRequest(
            text='{"entries": [{"type": "food", "items": [{"name": "гречка", "unit": "г"}]}]}'
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
        message="Не удалось получить structured payload от LLM."
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
    assert content[0]["type"] == "input_text"
    assert "json" in content[0]["text"]
    assert content[1] == {"type": "input_text", "text": "омлет на фото"}
    assert content[2]["type"] == "input_image"
    assert content[2]["image_url"].startswith("data:image/jpeg;base64,")
