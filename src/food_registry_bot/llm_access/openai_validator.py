from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenAIKeyValidationResult:
    is_valid: bool
    status: str
    error_message: str | None = None


def validate_openai_api_key(*, api_key: str, model: str) -> OpenAIKeyValidationResult:
    try:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            NotFoundError,
            OpenAI,
            PermissionDeniedError,
            RateLimitError,
        )
    except ImportError as exc:
        raise RuntimeError(
            "openai package is not installed. Add the dependency before validating personal OpenAI keys."
        ) from exc

    client = OpenAI(api_key=api_key)

    try:
        client.responses.create(
            model=model,
            input="ping",
            max_output_tokens=16,
        )
    except (AuthenticationError, PermissionDeniedError):
        return OpenAIKeyValidationResult(
            is_valid=False,
            status="invalid",
            error_message="OpenAI отклонил ключ или доступ к модели.",
        )
    except (BadRequestError, NotFoundError):
        return OpenAIKeyValidationResult(
            is_valid=False,
            status="invalid",
            error_message=f"Модель `{model}` недоступна для этого ключа или не существует.",
        )
    except (APIConnectionError, APITimeoutError, RateLimitError):
        return OpenAIKeyValidationResult(
            is_valid=False,
            status="unknown",
            error_message="Не удалось проверить ключ OpenAI из-за временной ошибки сети или лимита.",
        )
    except Exception as exc:
        return OpenAIKeyValidationResult(
            is_valid=False,
            status="unknown",
            error_message=f"Не удалось проверить ключ OpenAI: {exc}",
        )

    return OpenAIKeyValidationResult(
        is_valid=True,
        status="valid",
        error_message=None,
    )
