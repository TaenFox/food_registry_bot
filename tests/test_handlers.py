from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.handlers import handle_message, handle_recent, handle_start, handle_water_250_ml
from food_registry_bot.bot.keyboards import WATER_250_ML_BUTTON_TEXT
from food_registry_bot.db.base import Base
from food_registry_bot.db.models import Entry, EntryItem, EntryType, User


def create_session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


async def test_start_creates_user_on_first_message() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1001, username="new_user"),
        answer=AsyncMock(),
    )

    await handle_start(message, session_factory)

    with session_factory() as session:
        saved_user = session.query(User).filter_by(telegram_user_id=1001).one()

    assert saved_user.username == "new_user"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Привет. Профиль создан, бот готов принимать записи.",)
    assert message.answer.await_args.kwargs["reply_markup"] is not None


async def test_regular_message_reuses_existing_user() -> None:
    session_factory = create_session_factory()
    with session_factory() as session:
        session.add(User(telegram_user_id=1002, username="known_user", timezone="Europe/Moscow"))
        session.commit()

    message = SimpleNamespace(
        text="яблоко",
        from_user=SimpleNamespace(id=1002, username="known_user"),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory)

    with session_factory() as session:
        users_count = session.query(User).filter_by(telegram_user_id=1002).count()
        saved_entry = session.query(Entry).filter_by(user_id=1).one()
        saved_item = session.query(EntryItem).filter_by(entry_id=saved_entry.id).one()

    assert users_count == 1
    assert saved_entry.entry_type == EntryType.FOOD
    assert saved_entry.source_text == "яблоко"
    assert saved_item.name == "яблоко"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Сохранил:\n- яблоко",)
    assert message.answer.await_args.kwargs["reply_markup"] is not None


async def test_first_regular_message_creates_user_and_food_entry() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        text="гречка с курицей",
        from_user=SimpleNamespace(id=1004, username="food_user"),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory)

    with session_factory() as session:
        saved_user = session.query(User).filter_by(telegram_user_id=1004).one()
        saved_entry = session.query(Entry).filter_by(user_id=saved_user.id).one()
        saved_item = session.query(EntryItem).filter_by(entry_id=saved_entry.id).one()

    assert saved_entry.entry_type == EntryType.FOOD
    assert saved_entry.source_text == "гречка с курицей"
    assert saved_item.name == "гречка с курицей"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Сохранил:\n- гречка с курицей",)
    assert message.answer.await_args.kwargs["reply_markup"] is not None


async def test_json_message_creates_food_and_water_entries() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        text=(
            '{"entries": ['
            '{"type": "food", "items": [{"name": "гречка", "quantity": 200, "unit": "г"}]}, '
            '{"type": "water", "items": [{"name": "вода", "quantity": 250, "unit": "мл"}]}'
            "]}"
        ),
        from_user=SimpleNamespace(id=1005, username="json_user"),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory)

    with session_factory() as session:
        saved_user = session.query(User).filter_by(telegram_user_id=1005).one()
        saved_entries = session.query(Entry).filter_by(user_id=saved_user.id).order_by(Entry.id).all()
        saved_items = session.query(EntryItem).order_by(EntryItem.id).all()

    assert [entry.entry_type for entry in saved_entries] == [EntryType.FOOD, EntryType.WATER]
    assert saved_entries[0].source_text is None
    assert saved_entries[1].source_text is None
    assert saved_items[0].name == "гречка"
    assert saved_items[0].quantity == 200
    assert saved_items[0].unit == "g"
    assert saved_items[0].source_type == "extraction_payload"
    assert saved_items[1].name == "water"
    assert saved_items[1].quantity == 250
    assert saved_items[1].unit == "ml"
    assert saved_items[1].source_type == "extraction_payload"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- гречка: 200 г\n- вода: 250 мл",
    )
    assert message.answer.await_args.kwargs["reply_markup"] is not None


async def test_invalid_json_message_returns_validation_error() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        text='{"entries": [{"type": "food"}]}',
        from_user=SimpleNamespace(id=1006, username="bad_json_user"),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory)

    with session_factory() as session:
        entries_count = session.query(Entry).count()

    assert entries_count == 0
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Не удалось разобрать structured payload. Ожидаю объект вида {'entries': [...]} с type, items и name.",
    )
    assert message.answer.await_args.kwargs["reply_markup"] is not None


async def test_recent_returns_latest_entries_for_user() -> None:
    session_factory = create_session_factory()
    with session_factory() as session:
        user = User(telegram_user_id=1007, username="recent_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()

        first_entry = Entry(user_id=user.id, entry_type=EntryType.FOOD, source_text="яблоко", occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc))
        second_entry = Entry(user_id=user.id, entry_type=EntryType.WATER, source_text="250 мл", occurred_at=datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc))
        session.add_all([first_entry, second_entry])
        session.flush()

        session.add(EntryItem(entry_id=first_entry.id, position=0, name="яблоко"))
        session.add(EntryItem(entry_id=second_entry.id, position=0, name="water", quantity=250, unit="ml"))
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1007, username="recent_user"),
        answer=AsyncMock(),
    )

    await handle_recent(message, session_factory)

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Последние записи:\n- вода: 250 мл\n- яблоко",
    )
    assert message.answer.await_args.kwargs["reply_markup"] is not None


async def test_recent_returns_empty_state_when_no_entries_exist() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1008, username="empty_recent_user"),
        answer=AsyncMock(),
    )

    await handle_recent(message, session_factory)

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Пока нет сохранённых записей.",)
    assert message.answer.await_args.kwargs["reply_markup"] is not None


async def test_water_button_creates_water_entry() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        text=WATER_250_ML_BUTTON_TEXT,
        from_user=SimpleNamespace(id=1003, username="water_user"),
        answer=AsyncMock(),
    )

    await handle_water_250_ml(message, session_factory)

    with session_factory() as session:
        saved_user = session.query(User).filter_by(telegram_user_id=1003).one()
        saved_entry = session.query(Entry).filter_by(user_id=saved_user.id).one()
        saved_item = session.query(EntryItem).filter_by(entry_id=saved_entry.id).one()

    assert saved_entry.entry_type == EntryType.WATER
    assert saved_entry.source_text == "250 мл"
    assert saved_item.name == "water"
    assert saved_item.quantity == 250
    assert saved_item.unit == "ml"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Сохранил:\n- вода: 250 мл",)
    assert message.answer.await_args.kwargs["reply_markup"] is not None
