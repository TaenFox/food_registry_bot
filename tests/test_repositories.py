from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import EntryType
from food_registry_bot.db.repositories import EntryRepository, UserRepository


def create_test_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)()


def test_user_repository_creates_user_once() -> None:
    session = create_test_session()
    repository = UserRepository(session)

    user, created = repository.get_or_create(telegram_user_id=101, username="alice")
    same_user, created_again = repository.get_or_create(telegram_user_id=101, username="alice")

    assert created is True
    assert created_again is False
    assert user.id == same_user.id
    assert user.username == "alice"


def test_entry_repository_creates_entry_for_user() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=202, username="bob")

    entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        source_text="яблоко",
    )

    assert entry.id is not None
    assert entry.user_id == user.id
    assert entry.entry_type == EntryType.FOOD
    assert entry.source_text == "яблоко"
