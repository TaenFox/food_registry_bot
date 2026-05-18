from __future__ import annotations

import json
from datetime import datetime, timezone

from aiogram import Router
from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.keyboards import WATER_250_ML_BUTTON_TEXT, build_main_keyboard
from food_registry_bot.bot.payloads import NormalizedJournalPayload
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


def parse_normalized_journal_payload(message_text: str) -> NormalizedJournalPayload | None:
    stripped_text = message_text.strip()
    if not stripped_text.startswith("{"):
        return None

    return NormalizedJournalPayload.model_validate_json(stripped_text)


def format_saved_item_line(name: str, quantity: int | None, unit: str | None) -> str:
    if quantity is None:
        return f"- {name}"
    if unit is None:
        return f"- {name}: {quantity}"
    return f"- {name}: {quantity} {unit}"


def build_normalized_payload_confirmation(payload: NormalizedJournalPayload) -> str:
    lines = [f"Сохранил {len(payload.entries)} записей из JSON:"]
    for entry in payload.entries:
        for item in entry.items:
            lines.append(format_saved_item_line(item.name, item.quantity, item.unit))
    return "\n".join(lines)


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
    source_text = (message.text or "").strip()
    if not source_text:
        await message.answer(
            "Пока поддерживаются текстовые сообщения и кнопка воды.",
            reply_markup=build_main_keyboard(),
        )
        return

    try:
        normalized_payload = parse_normalized_journal_payload(source_text)
    except (ValidationError, json.JSONDecodeError):
        await message.answer(
            "Не удалось разобрать JSON. Ожидаю объект вида {'entries': [...]} с type и items.",
            reply_markup=build_main_keyboard(),
        )
        return

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)

        if normalized_payload is not None:
            for normalized_entry in normalized_payload.entries:
                EntryRepository(session).create(
                    user_id=user_id,
                    entry_type=normalized_entry.type,
                    occurred_at=normalized_entry.occurred_at or datetime.now(timezone.utc),
                    source_text=None,
                    items=[
                        EntryItemCreate(
                            name=item.name,
                            quantity=item.quantity,
                            unit=item.unit,
                            source_type="normalized_json",
                        )
                        for item in normalized_entry.items
                    ],
                )
        else:
            EntryRepository(session).create(
                user_id=user_id,
                entry_type=EntryType.FOOD,
                occurred_at=datetime.now(timezone.utc),
                source_text=source_text,
                items=[EntryItemCreate(name=source_text)],
            )

    if normalized_payload is not None:
        await message.answer(
            build_normalized_payload_confirmation(normalized_payload),
            reply_markup=build_main_keyboard(),
        )
        return

    await message.answer("Запись сохранена как еда.", reply_markup=build_main_keyboard())
