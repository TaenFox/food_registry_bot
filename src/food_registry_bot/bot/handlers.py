from __future__ import annotations

from io import BytesIO
from datetime import datetime, timezone

from aiogram import Router
from aiogram import F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.keyboards import WATER_250_ML_BUTTON_TEXT, build_main_keyboard
from food_registry_bot.db.models import EntryType
from food_registry_bot.db.repositories import (
    EntryItemCreate,
    EntryRepository,
    UserAccessRepository,
    UserRepository,
)
from food_registry_bot.db.session import session_scope
from food_registry_bot.extraction import (
    ExtractionImageInput,
    InvalidExtractionPayload,
    JournalExtractionService,
    JournalExtractionRequest,
    StructuredPayloadExtractionService,
    ValidExtractionPayload,
)
from food_registry_bot.nutrition import (
    FailedNutritionEstimation,
    NutritionEstimationService,
    SkippedNutritionEstimation,
    StaticNutritionEstimationService,
    StoredEntryNutritionEstimationUseCase,
    SuccessfulNutritionEstimation,
)

router = Router()
default_extraction_service = StructuredPayloadExtractionService()
default_nutrition_service = StaticNutritionEstimationService(raw_payload="")


class FoodWriteFlowError(RuntimeError):
    pass


def is_admin_user(telegram_user_id: int, admin_user_ids: tuple[int, ...]) -> bool:
    return telegram_user_id in admin_user_ids


def ensure_user_registered(message: Message, session: Session) -> tuple[bool, int]:
    telegram_user = message.from_user
    if telegram_user is None:
        raise ValueError("Incoming message does not contain Telegram user")

    user, created = UserRepository(session).get_or_create(
        telegram_user_id=telegram_user.id,
        username=telegram_user.username,
    )
    return created, user.id


def user_has_access(message: Message, session: Session, admin_user_ids: tuple[int, ...]) -> bool:
    telegram_user = message.from_user
    if telegram_user is None:
        return False

    if is_admin_user(telegram_user.id, admin_user_ids):
        return True

    return UserAccessRepository(session).is_allowed(telegram_user.id)


async def require_user_access(
    message: Message,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...],
) -> bool:
    with session_scope(session_factory) as session:
        telegram_user = message.from_user
        has_access = user_has_access(message, session, admin_user_ids)
        if not has_access and telegram_user is not None and not is_admin_user(telegram_user.id, admin_user_ids):
            UserAccessRepository(session).set_access(
                telegram_user_id=telegram_user.id,
                username=telegram_user.username,
                is_allowed=False,
            )

    if has_access:
        return True

    await message.answer("Доступ к боту не разрешён.")
    return False


def parse_target_telegram_user_id(command: CommandObject | None) -> int | None:
    if command is None or command.args is None:
        return None

    raw_value = command.args.strip()
    if not raw_value:
        return None

    try:
        return int(raw_value)
    except ValueError:
        return None


def build_admin_users_response(
    known_users: list,
    admin_user_ids: tuple[int, ...],
) -> str:
    if not known_users and not admin_user_ids:
        return "Пока нет известных пользователей."

    lines = ["Пользователи:"]
    rendered_ids: set[int] = set()

    for known_user in known_users:
        rendered_ids.add(known_user.telegram_user_id)
        username_suffix = f" @{known_user.username}" if known_user.username else ""
        if known_user.telegram_user_id in admin_user_ids:
            lines.append(f"- {known_user.telegram_user_id}{username_suffix} [admin]")
            continue

        status = "allowed" if known_user.is_allowed else "denied"
        lines.append(f"- {known_user.telegram_user_id}{username_suffix} [{status}]")
        next_command = (
            f"/admin_deny {known_user.telegram_user_id}"
            if known_user.is_allowed
            else f"/admin_allow {known_user.telegram_user_id}"
        )
        lines.append(next_command)

    for admin_user_id in sorted(admin_user_ids):
        if admin_user_id in rendered_ids:
            continue
        lines.append(f"- {admin_user_id} [admin]")

    return "\n".join(lines)


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


def build_extracted_payload_confirmation(payload, nutrition_result: SuccessfulNutritionEstimation | None = None) -> str:
    lines = ["Сохранил:"]
    for entry in payload.entries:
        for item in entry.items:
            lines.append(format_saved_item_line(item.name, item.quantity, item.unit))

    if nutrition_result is not None:
        lines.extend(
            [
                "",
                "КБЖУ по еде:",
                f"- калории: {round(nutrition_result.metric_totals.get('calories', 0.0), 1)} ккал",
                f"- белки: {round(nutrition_result.metric_totals.get('protein', 0.0), 1)} г",
                f"- жиры: {round(nutrition_result.metric_totals.get('fat', 0.0), 1)} г",
                f"- углеводы: {round(nutrition_result.metric_totals.get('carbs', 0.0), 1)} г",
            ]
        )

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


async def build_extraction_request(message: Message) -> JournalExtractionRequest | None:
    message_text = getattr(message, "text", None) or getattr(message, "caption", None)
    photo_sizes = getattr(message, "photo", None) or []
    if photo_sizes:
        photo_buffer = BytesIO()
        await message.bot.download(photo_sizes[-1], destination=photo_buffer)
        return JournalExtractionRequest(
            text=message_text,
            images=(ExtractionImageInput(data=photo_buffer.getvalue(), media_type="image/jpeg"),),
        )

    if message_text and message_text.strip():
        return JournalExtractionRequest(text=message_text)

    return None


@router.message(Command("admin_allow"))
async def handle_admin_allow(
    message: Message,
    command: CommandObject,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    telegram_user = message.from_user
    if telegram_user is None or not is_admin_user(telegram_user.id, admin_user_ids):
        await message.answer("Команда доступна только администратору.")
        return

    target_user_id = parse_target_telegram_user_id(command)
    if target_user_id is None:
        await message.answer("Использование: /admin_allow TELEGRAM_USER_ID")
        return

    with session_scope(session_factory) as session:
        UserAccessRepository(session).set_access(
            telegram_user_id=target_user_id,
            username=None,
            is_allowed=True,
        )

    await message.answer(f"Доступ разрешён для пользователя {target_user_id}.")


@router.message(Command("admin_deny"))
async def handle_admin_deny(
    message: Message,
    command: CommandObject,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    telegram_user = message.from_user
    if telegram_user is None or not is_admin_user(telegram_user.id, admin_user_ids):
        await message.answer("Команда доступна только администратору.")
        return

    target_user_id = parse_target_telegram_user_id(command)
    if target_user_id is None:
        await message.answer("Использование: /admin_deny TELEGRAM_USER_ID")
        return

    with session_scope(session_factory) as session:
        UserAccessRepository(session).set_access(
            telegram_user_id=target_user_id,
            username=None,
            is_allowed=False,
        )

    await message.answer(f"Доступ запрещён для пользователя {target_user_id}.")


@router.message(Command("admin_users"))
async def handle_admin_users(
    message: Message,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    telegram_user = message.from_user
    if telegram_user is None or not is_admin_user(telegram_user.id, admin_user_ids):
        await message.answer("Команда доступна только администратору.")
        return

    with session_scope(session_factory) as session:
        known_users = UserAccessRepository(session).list_known_users()

    await message.answer(build_admin_users_response(known_users, admin_user_ids))


@router.message(Command("start"))
async def handle_start(
    message: Message,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

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
async def handle_health(
    message: Message,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    await message.answer("ok")


@router.message(Command("recent"))
async def handle_recent(
    message: Message,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        entries = EntryRepository(session).list_recent_for_user(user_id=user_id, limit=5)

    await message.answer(build_recent_entries_response(entries), reply_markup=build_main_keyboard())


@router.message(F.text == WATER_250_ML_BUTTON_TEXT)
async def handle_water_250_ml(
    message: Message,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

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
    nutrition_service: NutritionEstimationService = default_nutrition_service,
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    extraction_request = await build_extraction_request(message)
    if extraction_request is None:
        await message.answer(
            "Пока поддерживаются текстовые сообщения, фото еды и кнопка воды.",
            reply_markup=build_main_keyboard(),
        )
        return

    extraction_result = extraction_service.extract(extraction_request)

    if isinstance(extraction_result, InvalidExtractionPayload):
        await message.answer(
            extraction_result.message,
            reply_markup=build_main_keyboard(),
        )
        return

    if extraction_result is None:
        await message.answer(
            "Текущий extraction provider не смог обработать сообщение с едой.",
            reply_markup=build_main_keyboard(),
        )
        return

    nutrition_result: SuccessfulNutritionEstimation | None = None
    try:
        with session_scope(session_factory) as session:
            _, user_id = ensure_user_registered(message, session)

            food_entry_ids: list[int] = []
            for extracted_entry in extraction_result.payload.entries:
                saved_entry = EntryRepository(session).create(
                    user_id=user_id,
                    entry_type=extracted_entry.type,
                    occurred_at=extracted_entry.occurred_at or datetime.now(timezone.utc),
                    source_text=None,
                    extraction_provider=extraction_result.extraction_provider,
                    extraction_model=extraction_result.extraction_model,
                    extraction_raw_payload=extraction_result.raw_payload,
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
                if extracted_entry.type is EntryType.FOOD:
                    food_entry_ids.append(saved_entry.id)

            if food_entry_ids:
                nutrition_flow_result = StoredEntryNutritionEstimationUseCase(
                    session,
                    nutrition_service,
                ).run(entry_ids=food_entry_ids)
                if isinstance(nutrition_flow_result, FailedNutritionEstimation):
                    raise FoodWriteFlowError(nutrition_flow_result.message)
                if isinstance(nutrition_flow_result, SkippedNutritionEstimation):
                    raise FoodWriteFlowError(nutrition_flow_result.reason)
                nutrition_result = nutrition_flow_result
    except FoodWriteFlowError as exc:
        await message.answer(str(exc), reply_markup=build_main_keyboard())
        return

    await message.answer(
        build_extracted_payload_confirmation(extraction_result.payload, nutrition_result),
        reply_markup=build_main_keyboard(),
    )
