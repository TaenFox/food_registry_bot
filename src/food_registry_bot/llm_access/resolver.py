from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from food_registry_bot.config import Settings
from food_registry_bot.conversation.factory import create_conversation_service
from food_registry_bot.conversation.mistral_client import MistralChatCompletionsConversationClient
from food_registry_bot.conversation.service import ConversationService, DisabledConversationService
from food_registry_bot.db.models import AccountCategory, LLMProvider, UserLLMSelectionMode
from food_registry_bot.db.repositories import (
    UserAccessRepository,
    UserLLMConnectionRepository,
    UserLLMProfileRepository,
)
from food_registry_bot.extraction.factory import create_extraction_service
from food_registry_bot.extraction.mistral_client import MistralChatCompletionsExtractionClient
from food_registry_bot.extraction.service import JournalExtractionService
from food_registry_bot.llm_access.crypto import SecretCipher, SecretCipherError
from food_registry_bot.nutrition.factory import create_nutrition_service
from food_registry_bot.nutrition.mistral_client import MistralChatCompletionsNutritionClient
from food_registry_bot.nutrition.service import NutritionEstimationService


@dataclass(frozen=True)
class ResolvedProviderAccess:
    is_available: bool
    source: str
    provider: str | None
    model: str | None
    api_key: str | None
    reason_code: str | None = None
    reason_message: str | None = None


@dataclass(frozen=True)
class UserLLMRuntimeBundle:
    extraction_service: JournalExtractionService
    nutrition_service: NutritionEstimationService
    conversation_service: ConversationService
    extraction_access: ResolvedProviderAccess | None
    nutrition_access: ResolvedProviderAccess | None
    conversation_access: ResolvedProviderAccess | None


def build_user_llm_runtime_bundle(
    *,
    session: Session,
    settings: Settings,
    user_id: int,
    telegram_user_id: int,
    admin_user_ids: tuple[int, ...],
    fallback_extraction_service: JournalExtractionService,
    fallback_nutrition_service: NutritionEstimationService,
    fallback_conversation_service: ConversationService,
) -> UserLLMRuntimeBundle:
    extraction_access = None
    nutrition_access = None
    conversation_access = None
    extraction_service = fallback_extraction_service
    nutrition_service = fallback_nutrition_service
    conversation_service = fallback_conversation_service

    if settings.extraction_provider.value == "llm":
        extraction_access = resolve_llm_provider_access(
            session=session,
            settings=settings,
            user_id=user_id,
            telegram_user_id=telegram_user_id,
            admin_user_ids=admin_user_ids,
            project_model=settings.llm_model,
        )
        if extraction_access.is_available and extraction_access.api_key is not None:
            extraction_service = create_extraction_service_for_access(
                settings=settings,
                access=extraction_access,
            )

    if settings.nutrition_provider.value == "llm":
        nutrition_access = resolve_llm_provider_access(
            session=session,
            settings=settings,
            user_id=user_id,
            telegram_user_id=telegram_user_id,
            admin_user_ids=admin_user_ids,
            project_model=settings.nutrition_model,
        )
        if nutrition_access.is_available and nutrition_access.api_key is not None:
            nutrition_service = create_nutrition_service_for_access(
                settings=settings,
                access=nutrition_access,
            )

    conversation_access = resolve_llm_provider_access(
        session=session,
        settings=settings,
        user_id=user_id,
        telegram_user_id=telegram_user_id,
        admin_user_ids=admin_user_ids,
        project_model=settings.conversation_model,
    )
    if conversation_access.is_available and conversation_access.api_key is not None:
        conversation_service = create_conversation_service_for_access(
            settings=settings,
            access=conversation_access,
        )
    else:
        conversation_service = DisabledConversationService()

    return UserLLMRuntimeBundle(
        extraction_service=extraction_service,
        nutrition_service=nutrition_service,
        conversation_service=conversation_service,
        extraction_access=extraction_access,
        nutrition_access=nutrition_access,
        conversation_access=conversation_access,
    )


def resolve_llm_provider_access(
    *,
    session: Session,
    settings: Settings,
    user_id: int,
    telegram_user_id: int,
    admin_user_ids: tuple[int, ...],
    project_model: str,
) -> ResolvedProviderAccess:
    if not settings.enable_openai_provider:
        return ResolvedProviderAccess(
            is_available=False,
            source="disabled",
            provider=LLMProvider.OPENAI.value,
            model=project_model,
            api_key=None,
            reason_code="provider_disabled",
            reason_message="Провайдер OpenAI сейчас отключён в конфигурации приложения.",
        )

    is_admin = telegram_user_id in admin_user_ids
    account_category = (
        AccountCategory.INTERNAL
        if is_admin
        else UserAccessRepository(session).get_effective_account_category(telegram_user_id)
    )
    profile, _created = UserLLMProfileRepository(session).get_or_create(user_id=user_id)

    if profile.selection_mode is UserLLMSelectionMode.PERSONAL or account_category is not AccountCategory.INTERNAL:
        return _resolve_selected_personal_access(
            session=session,
            settings=settings,
            user_id=user_id,
        )

    if settings.openai_api_key and settings.openai_api_key.strip():
        return ResolvedProviderAccess(
            is_available=True,
            source="project",
            provider=LLMProvider.OPENAI.value,
            model=project_model,
            api_key=settings.openai_api_key,
        )

    return ResolvedProviderAccess(
        is_available=False,
        source="project",
        provider=LLMProvider.OPENAI.value,
        model=project_model,
        api_key=None,
        reason_code="project_key_missing",
        reason_message="Проектный ключ OpenAI не настроен в конфигурации приложения.",
    )


def build_provider_unavailable_message(access: ResolvedProviderAccess | None) -> str:
    if access is None or access.is_available:
        return "LLM-провайдер недоступен."
    return access.reason_message or "Сейчас для этого действия нет доступного LLM-провайдера."


def create_extraction_service_for_access(
    *,
    settings: Settings,
    access: ResolvedProviderAccess,
) -> JournalExtractionService:
    if access.provider == LLMProvider.OPENAI.value and access.api_key is not None:
        settings_copy = settings.model_copy(
            update={"openai_api_key": access.api_key, "llm_model": access.model}
        )
        return create_extraction_service(settings_copy)
    if access.provider == LLMProvider.MISTRAL.value and access.api_key is not None and access.model is not None:
        return create_extraction_service(
            settings,
            llm_client=MistralChatCompletionsExtractionClient(
                api_key=access.api_key,
                model=access.model,
            ),
        )
    raise RuntimeError(f"Unsupported extraction provider access: {access.provider}")


def create_nutrition_service_for_access(
    *,
    settings: Settings,
    access: ResolvedProviderAccess,
) -> NutritionEstimationService:
    if access.provider == LLMProvider.OPENAI.value and access.api_key is not None:
        settings_copy = settings.model_copy(
            update={"openai_api_key": access.api_key, "nutrition_model": access.model}
        )
        return create_nutrition_service(settings_copy)
    if access.provider == LLMProvider.MISTRAL.value and access.api_key is not None and access.model is not None:
        return create_nutrition_service(
            settings,
            llm_client=MistralChatCompletionsNutritionClient(
                api_key=access.api_key,
                model=access.model,
            ),
        )
    raise RuntimeError(f"Unsupported nutrition provider access: {access.provider}")


def create_conversation_service_for_access(
    *,
    settings: Settings,
    access: ResolvedProviderAccess,
) -> ConversationService:
    if access.provider == LLMProvider.OPENAI.value and access.api_key is not None:
        settings_copy = settings.model_copy(
            update={"openai_api_key": access.api_key, "conversation_model": access.model}
        )
        return create_conversation_service(settings_copy)
    if access.provider == LLMProvider.MISTRAL.value and access.api_key is not None and access.model is not None:
        return create_conversation_service(
            settings,
            llm_client=MistralChatCompletionsConversationClient(
                api_key=access.api_key,
                model=access.model,
            ),
        )
    raise RuntimeError(f"Unsupported conversation provider access: {access.provider}")


def _resolve_selected_personal_access(
    *,
    session: Session,
    settings: Settings,
    user_id: int,
) -> ResolvedProviderAccess:
    connection = UserLLMConnectionRepository(session).get_selected_for_user(user_id=user_id)
    if connection is None:
        return ResolvedProviderAccess(
            is_available=False,
            source="personal",
            provider=None,
            model=None,
            api_key=None,
            reason_code="personal_key_missing",
            reason_message="Для этого действия не найдено выбранное персональное LLM-подключение.",
        )

    if not settings.personal_api_keys_secret or not settings.personal_api_keys_secret.strip():
        return ResolvedProviderAccess(
            is_available=False,
            source="personal",
            provider=connection.provider.value,
            model=connection.model,
            api_key=None,
            reason_code="personal_secret_missing",
            reason_message="В приложении не настроен секрет для расшифровки персональных API-ключей.",
        )

    if connection.provider is LLMProvider.OPENAI and not settings.enable_openai_provider:
        return ResolvedProviderAccess(
            is_available=False,
            source="personal",
            provider=connection.provider.value,
            model=connection.model,
            api_key=None,
            reason_code="provider_disabled",
            reason_message="Провайдер OpenAI сейчас отключён в конфигурации приложения.",
        )
    if connection.provider is LLMProvider.MISTRAL and not settings.enable_mistral_provider:
        return ResolvedProviderAccess(
            is_available=False,
            source="personal",
            provider=connection.provider.value,
            model=connection.model,
            api_key=None,
            reason_code="provider_disabled",
            reason_message="Провайдер Mistral сейчас отключён в конфигурации приложения.",
        )

    try:
        api_key = SecretCipher(settings.personal_api_keys_secret).decrypt(connection.encrypted_api_key)
    except SecretCipherError:
        return ResolvedProviderAccess(
            is_available=False,
            source="personal",
            provider=connection.provider.value,
            model=connection.model,
            api_key=None,
            reason_code="personal_key_decrypt_failed",
            reason_message="Не удалось расшифровать персональный LLM-ключ.",
        )

    return ResolvedProviderAccess(
        is_available=True,
        source="personal",
        provider=connection.provider.value,
        model=connection.model,
        api_key=api_key,
    )


def resolve_openai_provider_access(
    *,
    session: Session,
    settings: Settings,
    user_id: int,
    telegram_user_id: int,
    admin_user_ids: tuple[int, ...],
    model: str,
) -> ResolvedProviderAccess:
    return resolve_llm_provider_access(
        session=session,
        settings=settings,
        user_id=user_id,
        telegram_user_id=telegram_user_id,
        admin_user_ids=admin_user_ids,
        project_model=model,
    )
