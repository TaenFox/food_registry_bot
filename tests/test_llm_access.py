from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.config import Settings
from food_registry_bot.db.base import Base
from food_registry_bot.db.models import (
    AccountCategory,
    LLMProvider,
    SupportedMetric,
    UserLLMSelectionMode,
)
from food_registry_bot.db.repositories import (
    UserAccessRepository,
    UserLLMConnectionRepository,
    UserLLMProfileRepository,
    UserRepository,
)
from food_registry_bot.llm_access import SecretCipher
from food_registry_bot.llm_access.resolver import resolve_openai_provider_access


def create_test_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)()
    session.add_all(
        [
            SupportedMetric(code="calories", name="Calories", unit="kcal"),
            SupportedMetric(code="protein", name="Protein", unit="g"),
            SupportedMetric(code="fat", name="Fat", unit="g"),
            SupportedMetric(code="carbs", name="Carbs", unit="g"),
            SupportedMetric(code="fiber", name="Fiber", unit="g"),
        ]
    )
    session.commit()
    return session


def make_settings(**updates) -> Settings:
    settings = Settings(_env_file=None)
    for key, value in updates.items():
        setattr(settings, key, value)
    return settings


def test_secret_cipher_roundtrip() -> None:
    cipher = SecretCipher("test-secret")

    encrypted = cipher.encrypt("sk-test-123")

    assert encrypted != "sk-test-123"
    assert cipher.decrypt(encrypted) == "sk-test-123"


def test_resolve_openai_provider_access_uses_project_key_for_internal_user() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=1001, username="internal_user")
    UserAccessRepository(session).set_access(
        telegram_user_id=1001,
        username="internal_user",
        is_allowed=True,
        account_category=AccountCategory.INTERNAL,
    )
    settings = make_settings(
        openai_api_key="project-openai-key",
        enable_openai_provider=True,
    )

    access = resolve_openai_provider_access(
        session=session,
        settings=settings,
        user_id=user.id,
        telegram_user_id=1001,
        admin_user_ids=(),
        model=settings.conversation_model,
    )

    assert access.is_available is True
    assert access.source == "project"
    assert access.api_key == "project-openai-key"


def test_resolve_openai_provider_access_uses_personal_key_for_external_user() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=1002, username="external_user")
    UserAccessRepository(session).set_access(
        telegram_user_id=1002,
        username="external_user",
        is_allowed=True,
        account_category=AccountCategory.EXTERNAL,
    )
    settings = make_settings(
        openai_api_key="project-openai-key",
        enable_openai_provider=True,
        personal_api_keys_secret="local-secret",
    )
    encrypted_api_key = SecretCipher(settings.personal_api_keys_secret).encrypt("personal-openai-key")
    UserLLMConnectionRepository(session).upsert_connection(
        user_id=user.id,
        provider=LLMProvider.OPENAI,
        model=settings.conversation_model,
        encrypted_api_key=encrypted_api_key,
        is_selected=True,
    )
    UserLLMProfileRepository(session).set_selection_mode(
        user_id=user.id,
        selection_mode=UserLLMSelectionMode.PERSONAL,
    )

    access = resolve_openai_provider_access(
        session=session,
        settings=settings,
        user_id=user.id,
        telegram_user_id=1002,
        admin_user_ids=(),
        model=settings.conversation_model,
    )

    assert access.is_available is True
    assert access.source == "personal"
    assert access.api_key == "personal-openai-key"
