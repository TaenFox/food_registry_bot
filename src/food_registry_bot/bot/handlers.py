from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.keyboards import WATER_250_ML_BUTTON_TEXT, build_main_keyboard
from food_registry_bot.db.models import EntryType
from food_registry_bot.db.repositories import EntryItemCreate, EntryRepository, UserRepository
from food_registry_bot.db.session import session_scope

router = Router()


def ensure_user_registered(message: Message, session: Session) -> tuple[bool, int]:
    telegram_user = message.from_user
    if telegram_user is None:
        raise ValueError("Incoming message does not contain Telegram user")

    user, created = UserRepository(session).get_or_create(
        telegram_user_id=telegram_user.id,
        username=telegram_user.username,
    )
    return created, user.id


@router.message(Command("start"))
async def handle_start(message: Message, session_factory: sessionmaker[Session]) -> None:
    with session_scope(session_factory) as session:
        created, _ = ensure_user_registered(message, session)

    if created:
        await message.answer(
            "Привет. Профиль создан, бот готов принимать записи.",
            reply_markup=build_main_keyboard(),
        )
        return

    await message.answer(
        "Привет. Профиль уже существует, бот готов принимать записи.",
        reply_markup=build_main_keyboard(),
    )


@router.message(Command("health"))
async def handle_health(message: Message) -> None:
    await message.answer("ok")


@router.message(F.text == WATER_250_ML_BUTTON_TEXT)
async def handle_water_250_ml(message: Message, session_factory: sessionmaker[Session]) -> None:
    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        EntryRepository(session).create(
            user_id=user_id,
            entry_type=EntryType.WATER,
            occurred_at=datetime.now(timezone.utc),
            source_text="250 мл",
            items=[EntryItemCreate(name="water", quantity=250, unit="ml")],
        )

    await message.answer("Записал воду: 250 мл.", reply_markup=build_main_keyboard())


@router.message()
async def handle_message(message: Message, session_factory: sessionmaker[Session]) -> None:
    with session_scope(session_factory) as session:
        created, _ = ensure_user_registered(message, session)

    if created:
        await message.answer(
            "Профиль создан. Для первой записи можно нажать кнопку воды.",
            reply_markup=build_main_keyboard(),
        )
        return

    await message.answer(
        "Сообщение получено. Пока можно добавить воду кнопкой ниже.",
        reply_markup=build_main_keyboard(),
    )
