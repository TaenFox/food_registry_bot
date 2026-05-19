from __future__ import annotations

import asyncio
import html
from io import BytesIO
from datetime import date, datetime, timezone

from aiogram import Router
from aiogram import F
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.admin_backfill import AdminBackfillTracker
from food_registry_bot.bot.keyboards import (
    WATER_250_ML_BUTTON_TEXT,
    build_main_keyboard,
    build_summary_settings_keyboard,
)
from food_registry_bot.bot.payloads import SummarySettingsCallback
from food_registry_bot.db.models import EntryType
from food_registry_bot.db.session import session_scope
from food_registry_bot.db.repositories import (
    EntryItemCreate,
    EntryRepository,
    UserGoalPreferenceRepository,
    UserAccessRepository,
    UserRepository,
    UserSummaryPreferenceRepository,
)
from food_registry_bot.extraction import (
    ExtractionImageInput,
    InvalidExtractionPayload,
    JournalExtractionService,
    JournalExtractionRequest,
    StructuredPayloadExtractionService,
    ValidExtractionPayload,
)
from food_registry_bot.nutrition import (
    BackfillNutritionEstimationUseCase,
    DailyCalorieGoalSnapshotUseCase,
    DailyCalorieProgress,
    DailyCalorieProgressUseCase,
    DailyNutritionSummary,
    DailyNutritionSummaryUseCase,
    FailedNutritionEstimation,
    NutritionBackfillCompleted,
    NutritionBackfillProgress,
    NutritionEstimationService,
    resolve_local_summary_date,
    SUPPORTED_NUTRITION_METRIC_CODES,
    SkippedNutritionEstimation,
    StaticNutritionEstimationService,
    StoredEntryNutritionEstimationUseCase,
    SuccessfulNutritionEstimation,
)

router = Router()
default_extraction_service = StructuredPayloadExtractionService()
default_nutrition_service = StaticNutritionEstimationService(raw_payload="")
SUMMARY_METRIC_LINES = (
    ("calories", "К", "ккал"),
    ("protein", "Б", "г"),
    ("fat", "Ж", "г"),
    ("carbs", "У", "г"),
)


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


def parse_positive_int_arg(command: CommandObject | None) -> int | None:
    if command is None or command.args is None:
        return None

    raw_value = command.args.strip()
    if not raw_value:
        return None

    try:
        value = int(raw_value)
    except ValueError:
        return None

    if value <= 0:
        return None

    return value


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


def build_admin_backfill_response(result: NutritionBackfillCompleted, limit: int) -> str:
    if not result.selected_entry_ids:
        return f"Backfill nutrition: неполных food entries не найдено. Лимит {limit}."

    lines = [
        "Backfill nutrition завершён.",
        f"Выбрано entries: {len(result.selected_entry_ids)}",
        f"Обработано entries: {len(result.processed_entry_ids)}",
        f"Сохранено метрик: {result.saved_metric_count}",
    ]

    if result.skipped_entry_ids:
        lines.append(f"Пропущено entries: {', '.join(str(entry_id) for entry_id in result.skipped_entry_ids)}")

    if result.failed_entries:
        failed_ids = ", ".join(str(failure.entry_id) for failure in result.failed_entries)
        lines.append(f"Ошибки entries: {failed_ids}")

    return "\n".join(lines)


def build_admin_backfill_status_line(tracker: AdminBackfillTracker) -> str:
    snapshot = tracker.snapshot()
    remaining_count = max(
        snapshot.selected_entry_count
        - snapshot.processed_entry_count
        - snapshot.skipped_entry_count
        - snapshot.failed_entry_count,
        0,
    )
    if snapshot.state == "running":
        return (
            "running"
            f" (selected {snapshot.selected_entry_count},"
            f" processed {snapshot.processed_entry_count},"
            f" remaining {remaining_count},"
            f" skipped {snapshot.skipped_entry_count},"
            f" failed {snapshot.failed_entry_count},"
            f" limit {snapshot.limit})"
        )
    if snapshot.state == "completed":
        return (
            "completed"
            f" (selected {snapshot.selected_entry_count},"
            f" processed {snapshot.processed_entry_count},"
            f" skipped {snapshot.skipped_entry_count},"
            f" failed {snapshot.failed_entry_count},"
            f" limit {snapshot.limit})"
        )
    if snapshot.state == "failed":
        return (
            "failed"
            f" (selected {snapshot.selected_entry_count},"
            f" processed {snapshot.processed_entry_count},"
            f" remaining {remaining_count},"
            f" skipped {snapshot.skipped_entry_count},"
            f" failed {snapshot.failed_entry_count},"
            f" limit {snapshot.limit})"
        )
    return "idle"


def build_admin_overview_response(
    *,
    admin_user_id: int,
    admin_user_ids: tuple[int, ...],
    known_users: list,
    incomplete_food_entry_count: int,
    backfill_status_line: str,
) -> str:
    regular_known_users = [
        known_user
        for known_user in known_users
        if known_user.telegram_user_id not in admin_user_ids
    ]
    allowed_count = sum(1 for known_user in regular_known_users if known_user.is_allowed)
    denied_count = sum(1 for known_user in regular_known_users if not known_user.is_allowed)
    profile_count = sum(1 for known_user in regular_known_users if known_user.has_profile)

    return "\n".join(
        [
            "Admin dashboard:",
            f"- текущий админ: {admin_user_id}",
            f"- админов в конфиге: {len(admin_user_ids)}",
            f"- известных пользователей: {len(regular_known_users)}",
            f"- разрешённых пользователей: {allowed_count}",
            f"- запрещённых пользователей: {denied_count}",
            f"- пользователей с профилем: {profile_count}",
            f"- food entries без полного набора метрик: {incomplete_food_entry_count}",
            f"- backfill nutrition: {backfill_status_line}",
            "",
            "Доступные команды:",
            "- /admin",
            "- /admin_users",
            "- /admin_allow TELEGRAM_USER_ID",
            "- /admin_deny TELEGRAM_USER_ID",
            "- /admin_backfill_nutrition [LIMIT]",
        ]
    )


async def run_admin_backfill_task(
    *,
    chat_id: int,
    message_bot,
    session_factory: sessionmaker[Session],
    nutrition_service: NutritionEstimationService,
    tracker: AdminBackfillTracker,
    limit: int,
) -> None:
    try:
        def run_sync_backfill() -> NutritionBackfillCompleted:
            with session_scope(session_factory) as session:
                return BackfillNutritionEstimationUseCase(
                    session,
                    nutrition_service,
                ).run(
                    limit=limit,
                    progress_callback=lambda progress: tracker.update_progress(
                        selected_entry_count=progress.selected_entry_count,
                        processed_entry_count=progress.processed_entry_count,
                        skipped_entry_count=progress.skipped_entry_count,
                        failed_entry_count=progress.failed_entry_count,
                    ),
                )

        result = await asyncio.to_thread(run_sync_backfill)
        summary = build_admin_backfill_response(result, limit)
        tracker.mark_completed(message=summary)
        await message_bot.send_message(chat_id, summary)
    except Exception as exc:
        error_message = f"Backfill nutrition завершился с ошибкой: {exc}"
        tracker.mark_failed(message=error_message)
        await message_bot.send_message(chat_id, error_message)


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


def build_today_summary_response(summary: DailyNutritionSummary) -> str:
    if summary.included_entry_count == 0 and summary.excluded_entry_count == 0:
        return "Сегодня пока нет сохранённых записей еды."

    lines = ["Итог за сегодня:"]
    lines.append(f"- калории: {round(summary.totals.calories, 1)} ккал")

    if not summary.is_complete:
        lines.extend(
            [
                "",
                (
                    f"Есть записей еды без полного набора метрик: {summary.excluded_entry_count}."
                    " Итог дня пока неполный."
                ),
            ]
        )

    return "\n".join(lines)


def build_today_summary_response_with_preferences(
    summary: DailyNutritionSummary,
    *,
    enabled_metric_codes: tuple[str, ...],
    calorie_progress: DailyCalorieProgress | None = None,
) -> str:
    if summary.included_entry_count == 0 and summary.excluded_entry_count == 0:
        return "Сегодня пока нет сохранённых записей еды."
    if not enabled_metric_codes:
        return "В summary сейчас нет включённых показателей."
    lines: list[str] = []
    for metric_code, short_label, unit in SUMMARY_METRIC_LINES:
        if metric_code not in enabled_metric_codes:
            continue
        if metric_code == "calories" and calorie_progress is not None:
            lines.append(
                f"{short_label}: {round(calorie_progress.consumed_calories, 1)} / "
                f"{calorie_progress.goal_calories} {unit}"
            )
            continue

        metric_value = getattr(summary.totals, metric_code)
        lines.append(f"{short_label}: {round(metric_value, 1)} {unit}")

    rendered_summary = "<pre>" + html.escape("\n".join(lines)) + "</pre>"

    if not summary.is_complete:
        return "\n\n".join(
            [
                rendered_summary,
                (
                    f"Есть записей еды без полного набора метрик: {summary.excluded_entry_count}."
                    " Итог дня пока неполный."
                ),
            ]
        )

    return rendered_summary


def get_enabled_summary_metric_codes(preference) -> tuple[str, ...]:
    enabled_metric_codes: list[str] = []
    for metric_code, _short_label, _unit in SUMMARY_METRIC_LINES:
        if getattr(preference, f"show_{metric_code}"):
            enabled_metric_codes.append(metric_code)
    return tuple(enabled_metric_codes)


def build_summary_settings_response(
    *,
    show_calories: bool,
    show_protein: bool,
    show_fat: bool,
    show_carbs: bool,
    nutrition_day_start_hour: int,
) -> str:
    statuses = {
        True: "включено",
        False: "выключено",
    }
    return "\n".join(
        [
            "Настройки summary:",
            f"- калории: {statuses[show_calories]}",
            f"- белки: {statuses[show_protein]}",
            f"- жиры: {statuses[show_fat]}",
            f"- углеводы: {statuses[show_carbs]}",
            f"- начало дня: {nutrition_day_start_hour:02d}:00",
        ]
    )


def build_goal_response(
    *,
    calorie_goal: int | None,
    summary_date: date,
    snapshot_calorie_goal: int | None,
    timezone_name: str,
    nutrition_day_start_hour: int,
) -> str:
    if calorie_goal is None:
        return "Цель по калориям пока не настроена. Использование: /goal 1800"

    lines = [
        f"Текущая цель по калориям: {calorie_goal} ккал.",
        (
            f"Пищевой день {summary_date.isoformat()}: "
            f"{snapshot_calorie_goal if snapshot_calorie_goal is not None else 'не зафиксирована'}."
        ),
        f"Часовой пояс дня: {timezone_name}.",
        f"Начало пищевого дня: {nutrition_day_start_hour:02d}:00.",
    ]
    if snapshot_calorie_goal != calorie_goal:
        lines.extend(
            [
                "",
                "Текущий пищевой день уже был зафиксирован раньше, поэтому snapshot не изменился.",
            ]
        )
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


@router.message(Command("admin"))
async def handle_admin(
    message: Message,
    session_factory: sessionmaker[Session],
    backfill_tracker: AdminBackfillTracker,
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    telegram_user = message.from_user
    if telegram_user is None or not is_admin_user(telegram_user.id, admin_user_ids):
        await message.answer("Команда доступна только администратору.")
        return

    with session_scope(session_factory) as session:
        known_users = UserAccessRepository(session).list_known_users()
        incomplete_food_entry_count = EntryRepository(session).count_incomplete_food_entries(
            required_metric_codes=list(SUPPORTED_NUTRITION_METRIC_CODES)
        )

    await message.answer(
        build_admin_overview_response(
            admin_user_id=telegram_user.id,
            admin_user_ids=admin_user_ids,
            known_users=known_users,
            incomplete_food_entry_count=incomplete_food_entry_count,
            backfill_status_line=build_admin_backfill_status_line(backfill_tracker),
        )
    )


@router.message(Command("admin_backfill_nutrition"))
async def handle_admin_backfill_nutrition(
    message: Message,
    command: CommandObject,
    session_factory: sessionmaker[Session],
    nutrition_service: NutritionEstimationService = default_nutrition_service,
    backfill_tracker: AdminBackfillTracker | None = None,
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    telegram_user = message.from_user
    if telegram_user is None or not is_admin_user(telegram_user.id, admin_user_ids):
        await message.answer("Команда доступна только администратору.")
        return
    if backfill_tracker is None:
        raise RuntimeError("Backfill tracker is not configured")

    limit = 20
    if command.args is not None and command.args.strip():
        parsed_limit = parse_positive_int_arg(command)
        if parsed_limit is None:
            await message.answer("Использование: /admin_backfill_nutrition [LIMIT]")
            return
        limit = parsed_limit

    if backfill_tracker.is_running():
        await message.answer("Backfill nutrition уже выполняется.")
        return

    await message.answer(f"Запускаю backfill nutrition. Лимит: {limit}.")

    task = asyncio.create_task(
        run_admin_backfill_task(
            chat_id=message.chat.id,
            message_bot=message.bot,
            session_factory=session_factory,
            nutrition_service=nutrition_service,
            tracker=backfill_tracker,
            limit=limit,
        )
    )
    backfill_tracker.start(
        requested_by=telegram_user.id,
        limit=limit,
        task=task,
    )


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


@router.message(Command("settings"))
async def handle_settings(
    message: Message,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user_id)

    await message.answer(
        build_summary_settings_response(
            show_calories=preference.show_calories,
            show_protein=preference.show_protein,
            show_fat=preference.show_fat,
            show_carbs=preference.show_carbs,
            nutrition_day_start_hour=preference.nutrition_day_start_hour,
        ),
        reply_markup=build_summary_settings_keyboard(
            show_calories=preference.show_calories,
            show_protein=preference.show_protein,
            show_fat=preference.show_fat,
            show_carbs=preference.show_carbs,
            nutrition_day_start_hour=preference.nutrition_day_start_hour,
        ),
    )


@router.callback_query(SummarySettingsCallback.filter())
async def handle_toggle_summary_metric(
    callback: CallbackQuery,
    callback_data: SummarySettingsCallback,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    telegram_user = callback.from_user
    if telegram_user is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return
    if not (
        callback_data.action.startswith("toggle_")
        or callback_data.action == "cycle_nutrition_day_start_hour"
    ):
        await callback.answer("Неизвестное действие.", show_alert=True)
        return
    with session_scope(session_factory) as session:
        if not (
            is_admin_user(telegram_user.id, admin_user_ids)
            or UserAccessRepository(session).is_allowed(telegram_user.id)
        ):
            await callback.answer("Доступ к боту не разрешён.", show_alert=True)
            return

        user = UserRepository(session).get_by_telegram_user_id(telegram_user.id)
        if user is None:
            user, _created = UserRepository(session).get_or_create(
                telegram_user_id=telegram_user.id,
                username=telegram_user.username,
            )

        preference_repository = UserSummaryPreferenceRepository(session)
        if callback_data.action == "cycle_nutrition_day_start_hour":
            preference = preference_repository.cycle_nutrition_day_start_hour(user_id=user.id)
        else:
            metric_code = callback_data.action.removeprefix("toggle_")
            preference = preference_repository.toggle_metric_visibility(
                user_id=user.id,
                metric_code=metric_code,
            )

    if callback.message is not None:
        await callback.message.edit_text(
            build_summary_settings_response(
                show_calories=preference.show_calories,
                show_protein=preference.show_protein,
                show_fat=preference.show_fat,
                show_carbs=preference.show_carbs,
                nutrition_day_start_hour=preference.nutrition_day_start_hour,
            ),
            reply_markup=build_summary_settings_keyboard(
                show_calories=preference.show_calories,
                show_protein=preference.show_protein,
                show_fat=preference.show_fat,
                show_carbs=preference.show_carbs,
                nutrition_day_start_hour=preference.nutrition_day_start_hour,
            ),
        )
    await callback.answer("Настройка обновлена.")


@router.message(Command("today"))
async def handle_today(
    message: Message,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    telegram_user = message.from_user
    if telegram_user is None:
        raise ValueError("Incoming message does not contain Telegram user")

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        user = UserRepository(session).get_by_telegram_user_id(telegram_user.id)
        if user is None:
            raise RuntimeError("User profile was not found after registration")
        preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user_id)

        summary_date = resolve_local_summary_date(
            reference_at=datetime.now(timezone.utc),
            timezone_name=user.timezone,
            nutrition_day_start_hour=preference.nutrition_day_start_hour,
        )
        summary = DailyNutritionSummaryUseCase(session).run(
            user_id=user_id,
            timezone_name=user.timezone,
            summary_date=summary_date,
            nutrition_day_start_hour=preference.nutrition_day_start_hour,
        )
        goal_snapshot = DailyCalorieGoalSnapshotUseCase(session).get_or_create(
            user_id=user_id,
            summary_date=summary_date,
            timezone_name=user.timezone,
            nutrition_day_start_hour=preference.nutrition_day_start_hour,
        )
        calorie_progress = DailyCalorieProgressUseCase().build(
            summary=summary,
            snapshot=goal_snapshot,
        )

    await message.answer(
        build_today_summary_response_with_preferences(
            summary,
            enabled_metric_codes=get_enabled_summary_metric_codes(preference),
            calorie_progress=calorie_progress,
        ),
        reply_markup=build_main_keyboard(),
    )


@router.message(Command("goal"))
async def handle_goal(
    message: Message,
    command: CommandObject,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    telegram_user = message.from_user
    if telegram_user is None:
        raise ValueError("Incoming message does not contain Telegram user")

    parsed_goal = None
    if command.args is not None and command.args.strip():
        parsed_goal = parse_positive_int_arg(command)
        if parsed_goal is None:
            await message.answer("Использование: /goal 1800")
            return

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        user = UserRepository(session).get_by_telegram_user_id(telegram_user.id)
        if user is None:
            raise RuntimeError("User profile was not found after registration")

        summary_preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user_id)
        goal_preference_repository = UserGoalPreferenceRepository(session)
        if parsed_goal is not None:
            goal_preference = goal_preference_repository.set_calorie_goal(
                user_id=user_id,
                calorie_goal=parsed_goal,
            )
        else:
            goal_preference = goal_preference_repository.get_by_user_id(user_id)

        summary_date = resolve_local_summary_date(
            reference_at=datetime.now(timezone.utc),
            timezone_name=user.timezone,
            nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
        )
        snapshot = DailyCalorieGoalSnapshotUseCase(session).get_or_create(
            user_id=user_id,
            summary_date=summary_date,
            timezone_name=user.timezone,
            nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
        )
        calorie_goal_value = goal_preference.calorie_goal if goal_preference is not None else None
        snapshot_calorie_goal = snapshot.calorie_goal if snapshot is not None else None
        timezone_name = user.timezone
        nutrition_day_start_hour = summary_preference.nutrition_day_start_hour

    await message.answer(
        build_goal_response(
            calorie_goal=calorie_goal_value,
            summary_date=summary_date,
            snapshot_calorie_goal=snapshot_calorie_goal,
            timezone_name=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
        ),
        reply_markup=build_main_keyboard(),
    )


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
