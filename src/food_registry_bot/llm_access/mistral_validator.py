from __future__ import annotations

import httpx

from food_registry_bot.llm_access.openai_validator import OpenAIKeyValidationResult


MISTRAL_CHAT_COMPLETIONS_URL = "https://api.mistral.ai/v1/chat/completions"


def validate_mistral_api_key(*, api_key: str, model: str) -> OpenAIKeyValidationResult:
    try:
        response = httpx.post(
            MISTRAL_CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "response_format": {"type": "json_object"},
                "max_tokens": 16,
            },
            timeout=20.0,
        )
    except httpx.TimeoutException:
        return OpenAIKeyValidationResult(
            is_valid=False,
            status="unknown",
            error_message="Не удалось проверить ключ Mistral из-за таймаута.",
        )
    except httpx.HTTPError as exc:
        return OpenAIKeyValidationResult(
            is_valid=False,
            status="unknown",
            error_message=f"Не удалось проверить ключ Mistral: {exc}",
        )

    if response.status_code == 401:
        return OpenAIKeyValidationResult(
            is_valid=False,
            status="invalid",
            error_message="Mistral отклонил ключ.",
        )
    if response.status_code in (400, 404):
        return OpenAIKeyValidationResult(
            is_valid=False,
            status="invalid",
            error_message=f"Модель `{model}` недоступна для этого ключа Mistral или не существует.",
        )
    if response.status_code == 429 or 500 <= response.status_code < 600:
        return OpenAIKeyValidationResult(
            is_valid=False,
            status="unknown",
            error_message="Не удалось проверить ключ Mistral из-за временной ошибки сети или лимита.",
        )
    if response.is_error:
        return OpenAIKeyValidationResult(
            is_valid=False,
            status="unknown",
            error_message=f"Не удалось проверить ключ Mistral: HTTP {response.status_code}",
        )

    return OpenAIKeyValidationResult(
        is_valid=True,
        status="valid",
        error_message=None,
    )
