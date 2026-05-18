from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import EntryItem, EntryType
from food_registry_bot.db.repositories import EntryItemCreate, EntryRepository, UserRepository


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


def test_entry_repository_creates_water_entry() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=303, username="water")

    entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.WATER,
        occurred_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        source_text="250 мл",
        items=[EntryItemCreate(name="water", quantity=250, unit="ml")],
    )

    item = session.query(EntryItem).filter_by(entry_id=entry.id).one()

    assert entry.user_id == user.id
    assert entry.entry_type == EntryType.WATER
    assert entry.source_text == "250 мл"
    assert item.position == 0
    assert item.name == "water"
    assert item.quantity == 250
    assert item.unit == "ml"


def test_entry_repository_lists_recent_entries_in_descending_order() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=404, username="recent")
    repository = EntryRepository(session)

    older_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        source_text="яблоко",
        items=[EntryItemCreate(name="яблоко")],
    )
    newer_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.WATER,
        occurred_at=datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc),
        source_text="250 мл",
        items=[EntryItemCreate(name="water", quantity=250, unit="ml")],
    )

    recent_entries = repository.list_recent_for_user(user_id=user.id, limit=5)

    assert [entry.id for entry in recent_entries] == [newer_entry.id, older_entry.id]
