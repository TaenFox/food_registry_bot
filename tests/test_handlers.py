from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.handlers import handle_message, handle_start, handle_water_250_ml
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
        from_user=SimpleNamespace(id=1002, username="known_user"),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory)

    with session_factory() as session:
        users_count = session.query(User).filter_by(telegram_user_id=1002).count()

    assert users_count == 1
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Сообщение получено. Пока можно добавить воду кнопкой ниже.",)
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
    assert message.answer.await_args.args == ("Записал воду: 250 мл.",)
    assert message.answer.await_args.kwargs["reply_markup"] is not None
