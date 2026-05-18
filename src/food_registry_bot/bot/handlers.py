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
from food_registry_bot.extraction import (
    InvalidExtractionPayload,
    JournalExtractionService,
    StructuredPayloadExtractionService,
    ValidExtractionPayload,
)

router = Router()
default_extraction_service = StructuredPayloadExtractionService()


def ensure_user_registered(message: Message, session: Session) -> tuple[bool, int]:
    telegram_user = message.from_user
    if telegram_user is None:
        raise ValueError("Incoming message does not contain Telegram user")

    user, created = UserRepository(session).get_or_create(
        telegram_user_id=telegram_user.id,
        username=telegram_user.username,
    )
    return created, user.id


def present_item_name(name: str) -> str:
    if name == "water":
        return "вода"
    return name


def present_unit(unit: str | None) -> str | None:
    if unit == "ml":
        return "мл"
    if unit == "g":
        return "г"
    return unit


def format_saved_item_line(name: str, quantity: int | None, unit: str | None) -> str:
    presented_name = present_item_name(name)
    presented_unit = present_unit(unit)
    if quantity is None:
        return f"- {presented_name}"
    if presented_unit is None:
        return f"- {presented_name}: {quantity}"
    return f"- {presented_name}: {quantity} {presented_unit}"


def build_saved_items_confirmation(items: list[EntryItemCreate]) -> str:
    lines = ["Сохранил:"]
    for item in items:
        lines.append(format_saved_item_line(item.name, item.quantity, item.unit))
    return "\n".join(lines)


def build_extracted_payload_confirmation(payload) -> str:
    lines = ["Сохранил:"]
    for entry in payload.entries:
        for item in entry.items:
            lines.append(format_saved_item_line(item.name, item.quantity, item.unit))
    return "\n".join(lines)


def build_recent_entries_response(entries: list) -> str:
    if not entries:
        return "Пока нет сохранённых записей."

    lines = ["Последние записи:"]
    for entry in entries:
        item_texts = [
            format_saved_item_line(item.name, item.quantity, item.unit).removeprefix("- ")
            for item in sorted(entry.items, key=lambda current: current.position)
        ]
        if item_texts:
            lines.append("- " + ", ".join(item_texts))
        else:
            lines.append("- запись без позиций")
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


@router.message(Command("recent"))
async def handle_recent(message: Message, session_factory: sessionmaker[Session]) -> None:
    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        entries = EntryRepository(session).list_recent_for_user(user_id=user_id, limit=5)

    await message.answer(build_recent_entries_response(entries), reply_markup=build_main_keyboard())


@router.message(F.text == WATER_250_ML_BUTTON_TEXT)
async def handle_water_250_ml(message: Message, session_factory: sessionmaker[Session]) -> None:
    saved_items = [EntryItemCreate(name="water", quantity=250, unit="ml")]
    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        EntryRepository(session).create(
            user_id=user_id,
            entry_type=EntryType.WATER,
            occurred_at=datetime.now(timezone.utc),
            source_text="250 мл",
            items=saved_items,
        )

    await message.answer(build_saved_items_confirmation(saved_items), reply_markup=build_main_keyboard())


@router.message()
async def handle_message(
    message: Message,
    session_factory: sessionmaker[Session],
    extraction_service: JournalExtractionService = default_extraction_service,
) -> None:
    source_text = (message.text or "").strip()
    if not source_text:
        await message.answer(
            "Пока поддерживаются текстовые сообщения и кнопка воды.",
            reply_markup=build_main_keyboard(),
        )
        return

    extraction_result = extraction_service.extract_from_text(source_text)

    if isinstance(extraction_result, InvalidExtractionPayload):
        await message.answer(
            extraction_result.message,
            reply_markup=build_main_keyboard(),
        )
        return

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)

        if isinstance(extraction_result, ValidExtractionPayload):
            for extracted_entry in extraction_result.payload.entries:
                EntryRepository(session).create(
                    user_id=user_id,
                    entry_type=extracted_entry.type,
                    occurred_at=extracted_entry.occurred_at or datetime.now(timezone.utc),
                    source_text=None,
                    items=[
                        EntryItemCreate(
                            name=item.name,
                            quantity=item.quantity,
                            unit=item.unit,
                            source_type="extraction_payload",
                        )
                        for item in extracted_entry.items
                    ],
                )
        else:
            saved_items = [EntryItemCreate(name=source_text)]
            EntryRepository(session).create(
                user_id=user_id,
                entry_type=EntryType.FOOD,
                occurred_at=datetime.now(timezone.utc),
                source_text=source_text,
                items=saved_items,
            )

    if isinstance(extraction_result, ValidExtractionPayload):
        await message.answer(
            build_extracted_payload_confirmation(extraction_result.payload),
            reply_markup=build_main_keyboard(),
        )
        return

    await message.answer(build_saved_items_confirmation(saved_items), reply_markup=build_main_keyboard())
