from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.repositories import UserRepository
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
        await message.answer("Привет. Профиль создан, бот готов принимать записи.")
        return

    await message.answer("Привет. Профиль уже существует, бот готов принимать записи.")


@router.message(Command("health"))
async def handle_health(message: Message) -> None:
    await message.answer("ok")


@router.message()
async def handle_message(message: Message, session_factory: sessionmaker[Session]) -> None:
    with session_scope(session_factory) as session:
        created, _ = ensure_user_registered(message, session)

    if created:
        await message.answer("Профиль создан. Следующим шагом можно сохранять записи дневника.")
        return

    await message.answer("Сообщение получено. Базовый приём сообщений уже подключён.")
