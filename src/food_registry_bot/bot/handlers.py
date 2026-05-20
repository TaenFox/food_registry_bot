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
from food_registry_bot.bot.message_routing import (
    AMBIGUOUS,
    CONVERSATION,
    MessageRoutingService,
    RuleBasedMessageRoutingService,
)
from food_registry_bot.bot.payloads import SummarySettingsCallback
from food_registry_bot.conversation import (
    ConversationService,
    DisabledConversationService,
    NutritionCoachContextBuilder,
)
from food_registry_bot.db.models import ConversationMessageRole, EntryType
from food_registry_bot.db.session import session_scope
from food_registry_bot.db.repositories import (
    ConversationMessageRepository,
    ConversationSessionRepository,
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
)
from food_registry_bot.nutrition import (
    BackfillNutritionEstimationUseCase,
    DailyNutritionGoalProgress,
    DailyNutritionGoalProgressUseCase,
    DailyNutritionGoalSnapshotUseCase,
    DailyWaterSummary,
    DailyWaterSummaryUseCase,
    MetricGoalProgress,
    DailyNutritionSummary,
    DailyNutritionSummaryUseCase,
    FailedNutritionEstimation,
    NutritionBackfillCompleted,
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
default_conversation_service = DisabledConversationService()
default_message_routing_service = RuleBasedMessageRoutingService()
SUMMARY_METRIC_LINES = (
    ("calories", "К", "ккал"),
    ("protein", "Б", "г"),
    ("fat", "Ж", "г"),
    ("carbs", "У", "г"),
    ("fiber", "Кл", "г"),
    ("water", "В", "мл"),
)
GOAL_METRIC_LABELS = {
    "calories": "калории",
    "protein": "белки",
    "fat": "жиры",
    "carbs": "углеводы",
    "fiber": "клетчатка",
    "water": "вода",
}
SUMMARY_DISPLAY_MODE_LABELS = {
    "text": "текст",
    "bars": "бары",
}
BAR_MODE_LABELS = {
    "К": "Ккал",
}
BAR_MODE_LABEL_WIDTH = 6


class FoodWriteFlowError(RuntimeError):
    pass


def build_ambiguous_message_response() -> str:
    return (
        "Не понял, это запись в дневник или вопрос.\n"
        "Если хочешь сохранить факт, пришли явную запись еды или воды.\n"
        "Если хочешь совет или объяснение, задай вопрос прямо."
    )


def build_conversation_response(reply_text: str) -> str:
    return reply_text


def build_reply_kwargs(message: Message) -> dict[str, int]:
    message_id = getattr(message, "message_id", None)
    if message_id is None:
        return {}
    return {"reply_to_message_id": message_id}


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


def parse_goal_command_args(command: CommandObject | None) -> tuple[str, int] | None:
    if command is None or command.args is None:
        return None

    raw_args = command.args.strip()
    if not raw_args:
        return None

    parts = raw_args.split()
    if len(parts) == 1:
        try:
            value = int(parts[0])
        except ValueError:
            return None
        if value <= 0:
            return None
        return "calories", value

    if len(parts) != 2:
        return None

    metric_code, raw_value = parts
    if metric_code not in GOAL_METRIC_LABELS:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        return None
    if value <= 0:
        return None
    return metric_code, value


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
        lines.append(f"<code>{next_command}</code>")

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
            "- <code>/admin_allow TELEGRAM_USER_ID</code>",
            "- <code>/admin_deny TELEGRAM_USER_ID</code>",
            "- <code>/admin_backfill_nutrition [LIMIT]</code>",
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


def build_write_confirmation_response(saved_items: list[EntryItemCreate], day_report: str | None = None) -> str:
    saved_items_confirmation = build_saved_items_confirmation(saved_items)
    if day_report is None:
        return saved_items_confirmation
    return "\n\n".join([saved_items_confirmation, day_report])


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
    summary_display_mode: str = "text",
    goal_progress: DailyNutritionGoalProgress | None = None,
    water_summary: DailyWaterSummary | None = None,
    metric_deltas: dict[str, float] | None = None,
    show_post_entry_delta_suffix: bool = True,
) -> str:
    if (
        summary.included_entry_count == 0
        and summary.excluded_entry_count == 0
        and (water_summary is None or (
            water_summary.included_entry_count == 0 and water_summary.excluded_entry_count == 0
        ))
    ):
        return "Сегодня пока нет сохранённых записей."
    if not enabled_metric_codes:
        return "В summary сейчас нет включённых показателей."
    lines: list[str] = []
    for metric_code, short_label, unit in SUMMARY_METRIC_LINES:
        if metric_code not in enabled_metric_codes:
            continue
        metric_progress = getattr(goal_progress, metric_code) if goal_progress is not None else None
        metric_delta = 0.0 if metric_deltas is None else metric_deltas.get(metric_code, 0.0)
        if metric_progress is not None:
            if summary_display_mode == "bars":
                if metric_delta > 0:
                    lines.append(
                        build_metric_progress_delta_bar_line(
                            short_label,
                            unit,
                            metric_progress=metric_progress,
                            delta_value=metric_delta,
                            show_delta_suffix=show_post_entry_delta_suffix,
                        )
                    )
                else:
                    lines.append(build_metric_progress_bar_line(short_label, unit, metric_progress))
            else:
                line = (
                    f"{short_label}: {round(metric_progress.consumed_value, 1)} / "
                    f"{metric_progress.goal_value} {unit}"
                )
                if metric_delta > 0 and show_post_entry_delta_suffix:
                    line += f" (+{round(metric_delta, 1)} {unit})"
                lines.append(line)
            continue

        if metric_code == "water":
            metric_value = 0 if water_summary is None else water_summary.total_ml
        else:
            metric_value = getattr(summary.totals, metric_code)
        line = f"{short_label}: {round(metric_value, 1)} {unit}"
        if metric_delta > 0 and show_post_entry_delta_suffix:
            line += f" (+{round(metric_delta, 1)} {unit})"
        lines.append(line)

    rendered_summary = "<pre>" + html.escape("\n".join(lines)) + "</pre>"

    incompleteness_notes: list[str] = []
    if not summary.is_complete:
        incompleteness_notes.append(
            f"Есть записей еды без полного набора метрик: {summary.excluded_entry_count}. Итог дня пока неполный."
        )
    if water_summary is not None and not water_summary.is_complete:
        incompleteness_notes.append(
            f"Есть записей воды с неподдерживаемым форматом: {water_summary.excluded_entry_count}. Итог воды пока неполный."
        )
    if incompleteness_notes:
        return "\n\n".join([rendered_summary, *incompleteness_notes])

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
    show_fiber: bool,
    show_water: bool,
    show_post_entry_delta_suffix: bool,
    summary_display_mode: str,
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
            f"- клетчатка: {statuses[show_fiber]}",
            f"- вода: {statuses[show_water]}",
            f"- дельта записи: {statuses[show_post_entry_delta_suffix]}",
            f"- отображение: {SUMMARY_DISPLAY_MODE_LABELS[summary_display_mode]}",
            f"- начало дня: {nutrition_day_start_hour:02d}:00",
        ]
    )


def build_metric_progress_bar_line(
    short_label: str,
    unit: str,
    metric_progress: MetricGoalProgress,
) -> str:
    consumed = metric_progress.consumed_value
    goal = metric_progress.goal_value
    progress_ratio = consumed / goal if goal else 0.0
    filled_cells = min(int(progress_ratio * 10), 10)
    empty_cells = 10 - filled_cells
    base_bar = "[" + ("█" * filled_cells) + ("░" * empty_cells) + "]"
    overflow_cells = max(int((consumed - goal) / goal * 10), 0) if consumed > goal else 0
    overflow_bar = "█" * overflow_cells
    percentage = round(progress_ratio * 100, 1)
    rendered_label = BAR_MODE_LABELS.get(short_label, short_label).ljust(BAR_MODE_LABEL_WIDTH)
    return f"{rendered_label} {base_bar}{overflow_bar} {percentage}% {round(consumed, 1)}/{goal} {unit}"


def build_metric_progress_delta_bar_line(
    short_label: str,
    unit: str,
    *,
    metric_progress: MetricGoalProgress,
    delta_value: float,
    show_delta_suffix: bool,
) -> str:
    consumed = metric_progress.consumed_value
    goal = metric_progress.goal_value
    before_consumed = max(consumed - delta_value, 0.0)
    before_ratio = before_consumed / goal if goal else 0.0
    after_ratio = consumed / goal if goal else 0.0

    before_filled_cells = min(int(before_ratio * 10), 10)
    after_filled_cells = min(int(after_ratio * 10), 10)
    added_filled_cells = max(after_filled_cells - before_filled_cells, 0)
    empty_cells = 10 - after_filled_cells

    base_bar = (
        "["
        + ("█" * before_filled_cells)
        + ("▓" * added_filled_cells)
        + ("░" * empty_cells)
        + "]"
    )

    before_overflow_cells = max(int((before_consumed - goal) / goal * 10), 0) if before_consumed > goal else 0
    after_overflow_cells = max(int((consumed - goal) / goal * 10), 0) if consumed > goal else 0
    added_overflow_cells = max(after_overflow_cells - before_overflow_cells, 0)
    overflow_bar = ("█" * before_overflow_cells) + ("▓" * added_overflow_cells)
    percentage = round(after_ratio * 100, 1)
    rendered_label = BAR_MODE_LABELS.get(short_label, short_label).ljust(BAR_MODE_LABEL_WIDTH)
    line = (
        f"{rendered_label} {base_bar}{overflow_bar} {percentage}% "
        f"{round(consumed, 1)}/{goal} {unit}"
    )
    if show_delta_suffix:
        line += f" (+{round(delta_value, 1)} {unit})"
    return line


def build_saved_items_from_payload(payload) -> list[EntryItemCreate]:
    items: list[EntryItemCreate] = []
    for entry in payload.entries:
        for item in entry.items:
            items.append(
                EntryItemCreate(
                    name=item.name,
                    quantity=item.quantity,
                    unit=item.unit,
                )
            )
    return items


def resolve_metric_deltas(
    *,
    saved_items: list[EntryItemCreate],
    nutrition_result: SuccessfulNutritionEstimation | None,
) -> dict[str, float]:
    metric_deltas: dict[str, float] = {}
    if nutrition_result is not None:
        for metric_code, metric_value in nutrition_result.metric_totals.items():
            metric_deltas[metric_code] = metric_value

    water_delta = sum(
        item.quantity
        for item in saved_items
        if item.name == "water" and item.unit == "ml" and item.quantity is not None and item.quantity > 0
    )
    if water_delta > 0:
        metric_deltas["water"] = float(water_delta)

    return metric_deltas


def resolve_summary_dates_for_occurred_at_values(
    *,
    occurred_at_values: list[datetime],
    timezone_name: str,
    nutrition_day_start_hour: int,
) -> set[date]:
    return {
        resolve_local_summary_date(
            reference_at=occurred_at,
            timezone_name=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
        )
        for occurred_at in occurred_at_values
    }


def build_daily_report_for_summary_date(
    *,
    session: Session,
    user_id: int,
    timezone_name: str,
    summary_date: date,
    summary_preference,
    metric_deltas: dict[str, float] | None = None,
) -> str:
    summary = DailyNutritionSummaryUseCase(session).run(
        user_id=user_id,
        timezone_name=timezone_name,
        summary_date=summary_date,
        nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
    )
    water_summary = DailyWaterSummaryUseCase(session).run(
        user_id=user_id,
        timezone_name=timezone_name,
        summary_date=summary_date,
        nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
    )
    goal_snapshot = DailyNutritionGoalSnapshotUseCase(session).get_or_create(
        user_id=user_id,
        summary_date=summary_date,
        timezone_name=timezone_name,
        nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
    )
    goal_progress = DailyNutritionGoalProgressUseCase().build(
        summary=summary,
        water_summary=water_summary,
        snapshot=goal_snapshot,
    )
    return build_today_summary_response_with_preferences(
        summary,
        enabled_metric_codes=get_enabled_summary_metric_codes(summary_preference),
        summary_display_mode=summary_preference.summary_display_mode,
        goal_progress=goal_progress,
        water_summary=water_summary,
        metric_deltas=metric_deltas,
        show_post_entry_delta_suffix=summary_preference.show_post_entry_delta_suffix,
    )


def build_goal_response(
    *,
    goal_preference,
    enabled_metric_codes: tuple[str, ...],
    summary_date: date,
    goal_snapshot,
    timezone_name: str,
    nutrition_day_start_hour: int,
) -> str:
    lines = [
        "Текущие цели:",
        f"- калории: {goal_preference.calorie_goal} ккал",
        f"- белки: {goal_preference.protein_goal} г",
        f"- жиры: {goal_preference.fat_goal} г",
        f"- углеводы: {goal_preference.carbs_goal} г",
        f"- клетчатка: {goal_preference.fiber_goal} г",
        f"- вода: {goal_preference.water_goal} мл",
        f"Пищевой день {summary_date.isoformat()}:",
        f"- калории: {goal_snapshot.calorie_goal} ккал",
        f"- белки: {goal_snapshot.protein_goal} г",
        f"- жиры: {goal_snapshot.fat_goal} г",
        f"- углеводы: {goal_snapshot.carbs_goal} г",
        f"- клетчатка: {goal_snapshot.fiber_goal} г",
        f"- вода: {goal_snapshot.water_goal} мл",
        f"Часовой пояс дня: {timezone_name}.",
        f"Начало пищевого дня: {nutrition_day_start_hour:02d}:00.",
    ]
    goal_command_lines = []
    for metric_code in enabled_metric_codes:
        if metric_code == "calories":
            goal_command_lines.append(f"- <code>/goal {goal_preference.calorie_goal}</code>")
            continue
        if metric_code == "water":
            goal_command_lines.append(f"- <code>/goal water {goal_preference.water_goal}</code>")
            continue
        if metric_code == "fiber":
            goal_command_lines.append(f"- <code>/goal fiber {goal_preference.fiber_goal}</code>")
            continue
        goal_value = getattr(goal_preference, f"{metric_code}_goal")
        goal_command_lines.append(f"- <code>/goal {metric_code} {goal_value}</code>")

    if goal_command_lines:
        lines[7:7] = ["", "Настройка:", *goal_command_lines, ""]

    if (
        goal_snapshot.calorie_goal != goal_preference.calorie_goal
        or goal_snapshot.protein_goal != goal_preference.protein_goal
        or goal_snapshot.fat_goal != goal_preference.fat_goal
        or goal_snapshot.carbs_goal != goal_preference.carbs_goal
        or goal_snapshot.fiber_goal != goal_preference.fiber_goal
        or goal_snapshot.water_goal != goal_preference.water_goal
    ):
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
        await message.answer("Использование: <code>/admin_allow TELEGRAM_USER_ID</code>")
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
        await message.answer("Использование: <code>/admin_deny TELEGRAM_USER_ID</code>")
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
            await message.answer("Использование: <code>/admin_backfill_nutrition [LIMIT]</code>")
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
            show_fiber=preference.show_fiber,
            show_water=preference.show_water,
            show_post_entry_delta_suffix=preference.show_post_entry_delta_suffix,
            summary_display_mode=preference.summary_display_mode,
            nutrition_day_start_hour=preference.nutrition_day_start_hour,
        ),
        reply_markup=build_summary_settings_keyboard(
            show_calories=preference.show_calories,
            show_protein=preference.show_protein,
            show_fat=preference.show_fat,
            show_carbs=preference.show_carbs,
            show_fiber=preference.show_fiber,
            show_water=preference.show_water,
            show_post_entry_delta_suffix=preference.show_post_entry_delta_suffix,
            summary_display_mode=preference.summary_display_mode,
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
        or callback_data.action == "cycle_summary_display_mode"
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
        elif callback_data.action == "cycle_summary_display_mode":
            preference = preference_repository.cycle_summary_display_mode(user_id=user.id)
        elif callback_data.action == "toggle_post_entry_delta_suffix":
            preference = preference_repository.toggle_post_entry_delta_suffix(user_id=user.id)
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
                show_fiber=preference.show_fiber,
                show_water=preference.show_water,
                show_post_entry_delta_suffix=preference.show_post_entry_delta_suffix,
                summary_display_mode=preference.summary_display_mode,
                nutrition_day_start_hour=preference.nutrition_day_start_hour,
            ),
            reply_markup=build_summary_settings_keyboard(
                show_calories=preference.show_calories,
                show_protein=preference.show_protein,
                show_fat=preference.show_fat,
                show_carbs=preference.show_carbs,
                show_fiber=preference.show_fiber,
                show_water=preference.show_water,
                show_post_entry_delta_suffix=preference.show_post_entry_delta_suffix,
                summary_display_mode=preference.summary_display_mode,
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
        rendered_report = build_daily_report_for_summary_date(
            session=session,
            user_id=user_id,
            timezone_name=user.timezone,
            summary_date=summary_date,
            summary_preference=preference,
        )

    await message.answer(
        rendered_report,
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

    parsed_goal = parse_goal_command_args(command)
    if command.args is not None and command.args.strip() and parsed_goal is None:
        await message.answer(
            "Использование: <code>/goal 1800</code>, <code>/goal protein 90</code>, <code>/goal fiber 25</code> или <code>/goal water 2000</code>"
        )
        return

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        user = UserRepository(session).get_by_telegram_user_id(telegram_user.id)
        if user is None:
            raise RuntimeError("User profile was not found after registration")

        summary_preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user_id)
        goal_preference_repository = UserGoalPreferenceRepository(session)
        if parsed_goal is not None:
            metric_code, goal_value = parsed_goal
            goal_preference = goal_preference_repository.set_goal(
                user_id=user_id,
                metric_code=metric_code,
                goal_value=goal_value,
            )
        else:
            goal_preference, _created = goal_preference_repository.get_or_create(user_id=user_id)

        summary_date = resolve_local_summary_date(
            reference_at=datetime.now(timezone.utc),
            timezone_name=user.timezone,
            nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
        )
        snapshot = DailyNutritionGoalSnapshotUseCase(session).get_or_create(
            user_id=user_id,
            summary_date=summary_date,
            timezone_name=user.timezone,
            nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
        )
        timezone_name = user.timezone
        nutrition_day_start_hour = summary_preference.nutrition_day_start_hour

    await message.answer(
        build_goal_response(
            goal_preference=goal_preference,
            enabled_metric_codes=get_enabled_summary_metric_codes(summary_preference),
            summary_date=summary_date,
            goal_snapshot=snapshot,
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
    day_report: str | None = None
    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        user = UserRepository(session).get_by_telegram_user_id(message.from_user.id)
        if user is None:
            raise RuntimeError("User profile was not found after registration")
        preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user_id)
        occurred_at = datetime.now(timezone.utc)
        EntryRepository(session).create(
            user_id=user_id,
            entry_type=EntryType.WATER,
            occurred_at=occurred_at,
            source_text="250 мл",
            items=saved_items,
        )
        summary_date = resolve_local_summary_date(
            reference_at=occurred_at,
            timezone_name=user.timezone,
            nutrition_day_start_hour=preference.nutrition_day_start_hour,
        )
        day_report = build_daily_report_for_summary_date(
            session=session,
            user_id=user_id,
            timezone_name=user.timezone,
            summary_date=summary_date,
            summary_preference=preference,
            metric_deltas={"water": 250.0},
        )

    await message.answer(
        build_write_confirmation_response(saved_items, day_report),
        reply_markup=build_main_keyboard(),
    )


@router.message()
async def handle_message(
    message: Message,
    session_factory: sessionmaker[Session],
    extraction_service: JournalExtractionService = default_extraction_service,
    nutrition_service: NutritionEstimationService = default_nutrition_service,
    conversation_service: ConversationService = default_conversation_service,
    message_routing_service: MessageRoutingService = default_message_routing_service,
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    active_conversation_session_exists = False
    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        active_conversation_session_exists = (
            ConversationSessionRepository(session).get_active_for_user(
                user_id=user_id,
                reference_at=datetime.now(timezone.utc),
            )
            is not None
        )

    extraction_request = await build_extraction_request(message)
    if extraction_request is None:
        await message.answer(
            "Пока поддерживаются текстовые сообщения, фото еды и кнопка воды.",
            reply_markup=build_main_keyboard(),
        )
        return

    routing_decision = message_routing_service.route(
        extraction_request,
        has_active_conversation_session=active_conversation_session_exists,
    )
    if routing_decision.route == CONVERSATION:
        with session_scope(session_factory) as session:
            _, user_id = ensure_user_registered(message, session)
            user = UserRepository(session).get_by_telegram_user_id(message.from_user.id)
            if user is None:
                raise RuntimeError("User profile was not found after registration")
            summary_preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user_id)
            current_time = datetime.now(timezone.utc)
            conversation_session = ConversationSessionRepository(session).create_or_get_active(
                user_id=user_id,
                reference_at=current_time,
            )
            recent_turns = ConversationMessageRepository(session).list_recent_for_session(
                session_id=conversation_session.id,
                limit=6,
            )
            factual_context = NutritionCoachContextBuilder(session).build(
                user_id=user_id,
                timezone_name=user.timezone,
                nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
                reference_at=current_time,
            )
            conversation_reply = conversation_service.reply(
                user_message=extraction_request.text or "",
                factual_context=factual_context,
                session_summary=conversation_session.summary_text,
                recent_turns=[
                    turn for turn in recent_turns
                ],
            )
            ConversationMessageRepository(session).create(
                session_id=conversation_session.id,
                role=ConversationMessageRole.USER,
                content=extraction_request.text or "",
                created_at=current_time,
            )
            ConversationMessageRepository(session).create(
                session_id=conversation_session.id,
                role=ConversationMessageRole.ASSISTANT,
                content=conversation_reply.text,
                created_at=current_time,
            )
            ConversationSessionRepository(session).update_summary_and_touch(
                session_id=conversation_session.id,
                summary_text=conversation_reply.updated_session_summary,
                last_message_at=current_time,
            )
        await message.answer(
            build_conversation_response(conversation_reply.text),
            reply_markup=build_main_keyboard(),
            parse_mode=None,
            **build_reply_kwargs(message),
        )
        return

    if routing_decision.route == AMBIGUOUS:
        await message.answer(
            build_ambiguous_message_response(),
            reply_markup=build_main_keyboard(),
            **build_reply_kwargs(message),
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
    confirmation_text: str | None = None
    try:
        with session_scope(session_factory) as session:
            _, user_id = ensure_user_registered(message, session)
            user = UserRepository(session).get_by_telegram_user_id(message.from_user.id)
            if user is None:
                raise RuntimeError("User profile was not found after registration")
            summary_preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user_id)

            food_entry_ids: list[int] = []
            saved_items = build_saved_items_from_payload(extraction_result.payload)
            occurred_at_values: list[datetime] = []
            for extracted_entry in extraction_result.payload.entries:
                occurred_at = extracted_entry.occurred_at or datetime.now(timezone.utc)
                saved_entry = EntryRepository(session).create(
                    user_id=user_id,
                    entry_type=extracted_entry.type,
                    occurred_at=occurred_at,
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
                occurred_at_values.append(occurred_at)
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

            summary_dates = resolve_summary_dates_for_occurred_at_values(
                occurred_at_values=occurred_at_values,
                timezone_name=user.timezone,
                nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
            )
            if len(summary_dates) == 1:
                summary_date = next(iter(summary_dates))
                confirmation_text = build_write_confirmation_response(
                    saved_items,
                    build_daily_report_for_summary_date(
                        session=session,
                        user_id=user_id,
                        timezone_name=user.timezone,
                        summary_date=summary_date,
                        summary_preference=summary_preference,
                        metric_deltas=resolve_metric_deltas(
                            saved_items=saved_items,
                            nutrition_result=nutrition_result,
                        ),
                    ),
                )
            else:
                confirmation_text = build_write_confirmation_response(saved_items)
    except FoodWriteFlowError as exc:
        await message.answer(str(exc), reply_markup=build_main_keyboard())
        return

    await message.answer(
        confirmation_text,
        reply_markup=build_main_keyboard(),
    )
