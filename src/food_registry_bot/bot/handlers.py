from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import html
import json
from io import BytesIO
from pathlib import Path
import tempfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.admin_backfill import AdminBackfillTracker
from food_registry_bot.bot.keyboards import (
    WATER_250_ML_BUTTON_TEXT,
    build_admin_delete_entries_confirmation_keyboard,
    build_admin_llm_issues_keyboard,
    build_admin_overview_keyboard,
    build_admin_user_actions_keyboard,
    build_admin_user_list_keyboard,
    build_data_exchange_files_keyboard,
    build_goal_keyboard,
    build_main_keyboard,
    build_period_report_dynamics_keyboard,
    build_period_report_keyboard,
    build_period_report_noticeable_keyboard,
    build_recent_entries_delete_keyboard,
    build_recent_entry_confirmation_keyboard,
    build_recent_entry_selection_keyboard,
    build_summary_settings_keyboard,
)
from food_registry_bot.bot.message_routing import (
    AMBIGUOUS,
    CONVERSATION,
    MessageRoutingService,
    RuleBasedMessageRoutingService,
)
from food_registry_bot.bot.payloads import (
    AdminPanelCallback,
    DataExchangeFileCallback,
    GoalMessageCallback,
    PeriodReportCallback,
    RecentEntryDeleteCallback,
    SummarySettingsCallback,
)
from food_registry_bot.conversation import (
    ConversationService,
    DisabledConversationService,
    NutritionCoachContextBuilder,
)
from food_registry_bot.config import Settings, get_data_exchange_dir
from food_registry_bot.db.models import (
    AccountCategory,
    ConversationMessageRole,
    ConversationSession,
    EntryType,
    LLMConnectionValidationStatus,
    LLMProvider,
    UserLLMSelectionMode,
)
from food_registry_bot.db.models import DataExchangeDirection, DataExchangeFile, DataExchangeStatus, LLMIssueStage
from food_registry_bot.db.session import session_scope
from food_registry_bot.db.repositories import (
    ConversationMessageRepository,
    ConversationSessionRepository,
    DataExchangeFileRepository,
    EntryItemCreate,
    EntryItemMetricRepository,
    EntryItemMetricValue,
    EntryRepository,
    KnownUserAccessView,
    LLMIssueLogCreate,
    LLMIssueLogRepository,
    UserGoalPreferenceRepository,
    UserAccessRepository,
    UserLLMConnectionRepository,
    UserLLMProfileRepository,
    UserRepository,
    UserSummaryPreferenceRepository,
)
from food_registry_bot.exchange import DataExchangeService, DuplicateDataRowError, DuplicateFileError, UnsupportedExchangeFileError
from food_registry_bot.extraction import (
    ExtractionImageInput,
    InvalidExtractionPayload,
    JournalExtractionService,
    JournalExtractionRequest,
    StructuredPayloadExtractionService,
)
from food_registry_bot.exchange.service import FileLimitExceededError
from food_registry_bot.importing.csv_import import CSV_CONTRACT_TYPE_PARTIAL
from food_registry_bot.importing.csv_import import CSV_CONTRACT_TYPE_WORKOUT
from food_registry_bot.llm_access import SecretCipher
from food_registry_bot.llm_access.resolver import (
    build_provider_unavailable_message,
    build_user_llm_runtime_bundle,
)
from food_registry_bot.nutrition import (
    BackfillNutritionEstimationUseCase,
    calculate_default_workout_calorie_credit,
    DailyNutritionGoalProgress,
    DailyNutritionGoalProgressUseCase,
    DailyNutritionGoalSnapshotUseCase,
    DailyWorkoutCalorieCreditUseCase,
    DailyWaterSummary,
    DailyWaterSummaryUseCase,
    MetricGoalProgress,
    DailyNutritionSummary,
    DailyNutritionSummaryUseCase,
    FailedNutritionEstimation,
    PeriodMetricDynamics,
    PeriodReportUseCase,
    NutritionBackfillCompleted,
    NutritionEstimationService,
    resolve_day_bounds_utc,
    resolve_local_summary_date,
    resolve_workout_metric_value,
    SUPPORTED_NUTRITION_METRIC_CODES,
    SkippedNutritionEstimation,
    StaticNutritionEstimationService,
    StoredEntryNutritionEstimationUseCase,
    SuccessfulNutritionEstimation,
)

router = Router()
logger = logging.getLogger(__name__)
default_extraction_service = StructuredPayloadExtractionService()
default_nutrition_service = StaticNutritionEstimationService(raw_payload="")
default_conversation_service = DisabledConversationService()
default_message_routing_service = RuleBasedMessageRoutingService()
NUTRITION_COACH_DISPLAY_NAME = "Нутрициолог"
RECENT_ENTRIES_DEFAULT_COUNT = 5
RECENT_ENTRIES_MAX_COUNT = 60
RECENT_ENTRY_LIST_TITLE_MAX_LENGTH = 48
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
GOAL_STATUS_BELOW_EMOJI = "📉"
GOAL_STATUS_WITHIN_EMOJI = "🎯"
GOAL_STATUS_ABOVE_EMOJI = "📈"
EXCHANGE_STATUS_LABELS = {
    DataExchangeStatus.READY: "готов",
    DataExchangeStatus.PROCESSED: "обработан",
    DataExchangeStatus.ERROR: "ошибка",
}
EXCHANGE_DIRECTION_LABELS = {
    DataExchangeDirection.IMPORT: "импорт",
    DataExchangeDirection.EXPORT: "экспорт",
}
PERIOD_REPORT_PERIOD_SEQUENCE = (8, 16, 32)
DEFAULT_PERIOD_REPORT_DAYS = PERIOD_REPORT_PERIOD_SEQUENCE[0]
PERIOD_REPORT_SUBPERIOD_DAYS = 4
ADMIN_USER_PAGE_SIZE = 10
ADMIN_LLM_ISSUE_PAGE_SIZE = 5
LLM_MODEL_PLACEHOLDER = "<MODEL>"
API_KEY_PLACEHOLDER = "<API_KEY>"


class FoodWriteFlowError(RuntimeError):
    def __init__(self, message: str, *, issue=None) -> None:
        super().__init__(message)
        self.issue = issue


def truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def describe_import_contract(contract_type: str) -> str:
    if contract_type == CSV_CONTRACT_TYPE_WORKOUT:
        return "тренировки"
    if contract_type == CSV_CONTRACT_TYPE_PARTIAL:
        return "еда и вода (неполный файл)"
    return "еда и вода"


def build_import_validation_response_text(validation_result) -> str:
    contract_label = describe_import_contract(validation_result.contract_type)
    if validation_result.contract_type == CSV_CONTRACT_TYPE_WORKOUT:
        return (
            "Файл принят и подготовлен к импорту.\n\n"
            f"Распознан тип файла: {contract_label}\n\n"
            "Будет создано:\n"
            f"- тренировок: {validation_result.workout_entry_count}\n\n"
            "Диапазон дат:\n"
            f"- {validation_result.date_from.isoformat() if validation_result.date_from else '—'} — "
            f"{validation_result.date_to.isoformat() if validation_result.date_to else '—'}\n\n"
            "Статус файла: готов\n"
            "Открыть список файлов: /files"
        )
    if validation_result.contract_type == CSV_CONTRACT_TYPE_PARTIAL:
        return (
            "Файл принят и подготовлен к импорту.\n\n"
            f"Распознан тип файла: {contract_label}\n\n"
            "Будет создано:\n"
            f"- записей еды: {validation_result.food_entry_count}\n"
            f"- записей воды: {validation_result.water_entry_count}\n\n"
            "Диапазон дат:\n"
            f"- {validation_result.date_from.isoformat() if validation_result.date_from else '—'} — "
            f"{validation_result.date_to.isoformat() if validation_result.date_to else '—'}\n\n"
            "Статус файла: готов\n"
            "Файл содержит неполный набор данных.\n"
            "После импорта часть итогов дня может быть неполной.\n"
            "Если понадобится дозаполнение метрик, попроси администратора запустить /admin_backfill_nutrition.\n"
            "Открыть список файлов: /files"
        )
    return (
        "Файл принят и подготовлен к импорту.\n\n"
        f"Распознан тип файла: {contract_label}\n\n"
        "Будет создано:\n"
        f"- записей еды: {validation_result.food_entry_count}\n"
        f"- записей воды: {validation_result.water_entry_count}\n\n"
        "Диапазон дат:\n"
        f"- {validation_result.date_from.isoformat() if validation_result.date_from else '—'} — "
        f"{validation_result.date_to.isoformat() if validation_result.date_to else '—'}\n\n"
        "Статус файла: готов\n"
        "Открыть список файлов: /files"
    )


def build_data_exchange_files_response(files: list[DataExchangeFile]) -> str:
    if not files:
        return (
            "Файлов пока нет.\n"
            "Что можно сделать:\n"
            "- загрузить CSV-файл для проверки и подготовки импорта;\n"
            "- создать экспорт через кнопку ниже."
        )

    lines = ["Файлы:"]
    for index, exchange_file in enumerate(files, start=1):
        lines.append(f"{index}. [#{exchange_file.id}] {exchange_file.original_filename}")
        lines.append(
            f"   {EXCHANGE_DIRECTION_LABELS[exchange_file.direction]} · статус: {EXCHANGE_STATUS_LABELS[exchange_file.status]}"
        )
        lines.append(f"   тип: {describe_import_contract(exchange_file.contract_type)}")
        if exchange_file.direction is DataExchangeDirection.IMPORT:
            if exchange_file.contract_type == CSV_CONTRACT_TYPE_WORKOUT:
                lines.append(f"   тренировок: {exchange_file.row_count}")
            else:
                lines.append(
                    f"   еда: {exchange_file.food_entry_count}, вода: {exchange_file.water_entry_count}"
                )
        else:
            if exchange_file.contract_type == CSV_CONTRACT_TYPE_WORKOUT:
                lines.append(f"   тренировок: {exchange_file.row_count}")
            else:
                lines.append(
                    f"   строк: {exchange_file.row_count}, еда: {exchange_file.food_entry_count}, вода: {exchange_file.water_entry_count}"
                )
        if exchange_file.date_from is not None and exchange_file.date_to is not None:
            lines.append(f"   даты: {exchange_file.date_from.isoformat()} — {exchange_file.date_to.isoformat()}")
        if exchange_file.contract_type == CSV_CONTRACT_TYPE_PARTIAL:
            lines.append("   после импорта часть итогов может быть неполной")
    return "\n".join(lines)


async def download_message_document_to_temp_file(message: Message) -> tuple[Path, str]:
    document = getattr(message, "document", None)
    if document is None:
        raise ValueError("Incoming message does not contain a document")
    original_filename = document.file_name or "upload.csv"
    exchange_dir = get_data_exchange_dir()
    upload_dir = exchange_dir / "_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="upload_",
        suffix=".csv",
        dir=upload_dir,
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
    await message.bot.download(document, destination=temp_path)
    return temp_path, original_filename


async def render_data_exchange_files_message(
    target,
    *,
    files: list[DataExchangeFile],
) -> None:
    text = build_data_exchange_files_response(files)
    reply_markup = build_data_exchange_files_keyboard(files=files)
    if isinstance(target, Message):
        await target.answer(text, reply_markup=reply_markup)
        return
    await safe_edit_message_text(target.message, text=text, reply_markup=reply_markup)


async def safe_edit_message_text(message: Message, *, text: str, reply_markup) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return
        raise


async def safe_delete_message(message: Message) -> None:
    with suppress(TelegramBadRequest):
        await message.delete()


def build_ambiguous_message_response() -> str:
    return (
        "Не до конца понял, это запись в дневник или вопрос.\n"
        "Если хочешь сохранить запись, напиши еду или воду прямо, например: `гречка с курицей` или `вода 300 мл`.\n"
        "Если хочешь совет, задай вопрос текстом, например: `как добрать белок сегодня?`"
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

    await message.answer(
        "Сейчас у тебя нет доступа к боту. Если он нужен, попроси администратора добавить твой Telegram ID."
    )
    return False


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


def parse_provider_save_command_args(command: CommandObject | None) -> tuple[str, str] | None:
    if command is None or command.args is None:
        return None

    parts = command.args.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    model, api_key = parts
    if not model.strip() or not api_key.strip():
        return None
    return model.strip(), api_key.strip()


def parse_provider_model_arg(command: CommandObject | None) -> str | None:
    if command is None or command.args is None:
        return None
    model = command.args.strip()
    return model or None


def resolve_effective_account_category(
    *,
    telegram_user_id: int,
    access_account_category: str,
    admin_user_ids: tuple[int, ...],
) -> str:
    if is_admin_user(telegram_user_id, admin_user_ids):
        return AccountCategory.INTERNAL.value
    return access_account_category


def build_provider_settings_response(
    *,
    telegram_user_id: int,
    account_category: str,
    selection_mode: str,
    project_openai_enabled: bool,
    personal_openai_enabled: bool,
    connections: list,
    admin_user_ids: tuple[int, ...],
) -> str:
    lines = ["LLM-провайдеры:"]
    lines.append(f"- категория аккаунта: {resolve_effective_account_category(telegram_user_id=telegram_user_id, access_account_category=account_category, admin_user_ids=admin_user_ids)}")
    lines.append(f"- режим выбора: {selection_mode}")
    lines.append(f"- проектный OpenAI: {'доступен' if project_openai_enabled else 'недоступен'}")
    lines.append(f"- персональные ключи OpenAI: {'доступны' if personal_openai_enabled else 'недоступны'}")
    if not connections:
        lines.extend(
            [
                "",
                "Сохранённых персональных подключений пока нет.",
                "Команды:",
                f"- /provider_save_openai {LLM_MODEL_PLACEHOLDER} {API_KEY_PLACEHOLDER}",
                "- /provider_use_personal",
                "- /provider_use_project",
            ]
        )
        return "\n".join(lines)

    lines.extend(["", "Сохранённые подключения:"])
    for connection in connections:
        selected_suffix = " [selected]" if connection.is_selected else ""
        enabled_suffix = "on" if connection.is_enabled else "off"
        lines.append(
            f"- {connection.provider}/{connection.model} · {enabled_suffix} · {connection.validation_status}{selected_suffix}"
        )
        if connection.validation_error:
            lines.append(f"  ошибка: {truncate_text(connection.validation_error, limit=120)}")
    lines.extend(
        [
            "",
            "Команды:",
            f"- /provider_save_openai {LLM_MODEL_PLACEHOLDER} {API_KEY_PLACEHOLDER}",
            "- /provider_use_personal",
            "- /provider_use_project",
        ]
    )
    return "\n".join(lines)


def build_admin_user_directory(known_users: list, admin_user_ids: tuple[int, ...]) -> list:
    users_by_id = {known_user.telegram_user_id: known_user for known_user in known_users}
    for admin_user_id in admin_user_ids:
        if admin_user_id in users_by_id:
            continue
        users_by_id[admin_user_id] = KnownUserAccessView(
            telegram_user_id=admin_user_id,
            username=None,
            has_profile=False,
            is_allowed=False,
            account_category=AccountCategory.INTERNAL.value,
        )
    admin_entries = [users_by_id[telegram_user_id] for telegram_user_id in sorted(admin_user_ids) if telegram_user_id in users_by_id]
    regular_entries = [
        users_by_id[telegram_user_id]
        for telegram_user_id in sorted(users_by_id)
        if telegram_user_id not in admin_user_ids
    ]
    return [*admin_entries, *regular_entries]


def filter_manageable_known_users(known_users: list, admin_user_ids: tuple[int, ...]) -> list:
    return [known_user for known_user in build_admin_user_directory(known_users, admin_user_ids) if known_user.telegram_user_id not in admin_user_ids]


def build_admin_user_button_label(known_user, *, is_admin: bool) -> str:
    username_part = f"@{known_user.username}" if known_user.username else str(known_user.telegram_user_id)
    status_part = "admin" if is_admin else ("on" if known_user.is_allowed else "off")
    return truncate_button_label(f"{known_user.telegram_user_id} · {username_part} · {status_part}", max_length=40)


def build_admin_users_page_response(known_users: list, *, page: int, page_size: int, admin_user_ids: tuple[int, ...]) -> str:
    if not known_users:
        return "Пользователей для управления пока нет."

    lines = [f"Пользователи (страница {page + 1}, по {page_size}):"]
    for known_user in known_users:
        username_suffix = f" @{known_user.username}" if known_user.username else ""
        is_admin = known_user.telegram_user_id in admin_user_ids
        status = "admin" if is_admin else ("доступ разрешён" if known_user.is_allowed else "доступ запрещён")
        profile_status = "профиль есть" if known_user.has_profile else "профиля нет"
        lines.append(
            f"- {known_user.telegram_user_id}{username_suffix} [{status}; {profile_status}]"
        )
    lines.extend(["", "Выбери пользователя кнопкой ниже."])
    return "\n".join(lines)


def build_admin_user_actions_response(*, known_user, entry_count: int, is_admin: bool) -> str:
    username_suffix = f"@{known_user.username}" if known_user.username else "—"
    access_status = "admin" if is_admin else ("разрешён" if known_user.is_allowed else "запрещён")
    profile_status = "есть" if known_user.has_profile else "нет"
    return "\n".join(
        [
            "Пользователь:",
            f"- Telegram ID: {known_user.telegram_user_id}",
            f"- username: {username_suffix}",
            f"- доступ: {access_status}",
            f"- профиль: {profile_status}",
            f"- записей в журнале: {entry_count}",
        ]
    )


def build_admin_delete_entries_prompt(*, telegram_user_id: int, entry_count: int) -> str:
    return "\n".join(
        [
            "Подтверди удаление данных пользователя.",
            f"Telegram ID: {telegram_user_id}",
            f"Будет удалено записей: {entry_count}",
        ]
    )


def build_admin_backfill_response(result: NutritionBackfillCompleted, limit: int) -> str:
    if not result.selected_entry_ids:
        return f"Дозаполнение nutrition metrics: неполных записей еды не найдено. Лимит {limit}."

    lines = [
        "Дозаполнение nutrition metrics завершено.",
        f"Выбрано записей: {len(result.selected_entry_ids)}",
        f"Обработано записей: {len(result.processed_entry_ids)}",
        f"Сохранено метрик: {result.saved_metric_count}",
    ]

    if result.skipped_entry_ids:
        lines.append(f"Пропущено записей: {', '.join(str(entry_id) for entry_id in result.skipped_entry_ids)}")

    if result.failed_entries:
        failed_ids = ", ".join(str(failure.entry_id) for failure in result.failed_entries)
        lines.append(f"Ошибки по записям: {failed_ids}")

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
    recent_extraction_issue_count: int,
    recent_nutrition_issue_count: int,
    backfill_status_line: str,
    app_version: str,
) -> str:
    regular_known_users = filter_manageable_known_users(known_users, admin_user_ids)
    allowed_count = sum(1 for known_user in regular_known_users if known_user.is_allowed)
    denied_count = sum(1 for known_user in regular_known_users if not known_user.is_allowed)
    profile_count = sum(1 for known_user in regular_known_users if known_user.has_profile)

    return "\n".join(
        [
            "Панель администратора:",
            f"- версия бота: {app_version}",
            f"- текущий админ: {admin_user_id}",
            f"- админов в конфиге: {len(admin_user_ids)}",
            f"- известных пользователей: {len(regular_known_users)}",
            f"- разрешённых пользователей: {allowed_count}",
            f"- запрещённых пользователей: {denied_count}",
            f"- пользователей с профилем: {profile_count}",
            f"- food entries без полного набора метрик: {incomplete_food_entry_count}",
            f"- LLM extraction issues за 24ч: {recent_extraction_issue_count}",
            f"- LLM nutrition issues за 24ч: {recent_nutrition_issue_count}",
            f"- дозаполнение nutrition metrics: {backfill_status_line}",
            "",
            "Доступные действия:",
            "- кнопка «Управление пользователями»",
            "- /admin",
            "- <code>/admin_backfill_nutrition [LIMIT]</code>",
        ]
    )


def load_admin_known_users_page(
    *,
    known_users: list,
    page: int,
    page_size: int = ADMIN_USER_PAGE_SIZE,
) -> tuple[int, list, bool, bool]:
    page = max(page, 0)
    while True:
        start = page * page_size
        if known_users or page == 0:
            page_entries = known_users[start:start + page_size]
            has_next_page = start + page_size < len(known_users)
            return page, page_entries, page > 0, has_next_page
        page -= 1


def find_known_user(known_users: list, *, telegram_user_id: int):
    for known_user in known_users:
        if known_user.telegram_user_id == telegram_user_id:
            return known_user
    return None


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
        error_message = f"Дозаполнение nutrition metrics завершилось с ошибкой: {exc}"
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
    if unit == "min":
        return "мин"
    return unit


def format_saved_item_line(name: str, quantity: int | None, unit: str | None) -> str:
    presented_name = present_item_name(name)
    presented_unit = present_unit(unit)
    if quantity is None:
        return f"- {presented_name}"
    if presented_unit is None:
        return f"- {presented_name} ({quantity})"
    return f"- {presented_name} ({quantity} {presented_unit})"


def build_saved_items_confirmation(items: list[EntryItemCreate]) -> str:
    lines = ["Сохранил:"]
    for item in items:
        lines.append(format_saved_item_line(item.name, item.quantity, item.unit))
    return "\n".join(lines)


def build_llm_issue_log_summary(*, page: int, page_size: int, issues: list) -> str:
    if not issues:
        return f"LLM-ошибок не найдено. Страница {page + 1}."

    lines = [f"Последние LLM-ошибки. Страница {page + 1}, по {page_size}."]
    for issue in issues:
        provider_part = issue.provider or "unknown"
        model_part = issue.model or "unknown"
        user_part = (
            f"{issue.telegram_user_id}"
            if issue.telegram_user_id is not None
            else "unknown"
        )
        lines.extend(
            [
                "",
                f"- {issue.created_at.strftime('%Y-%m-%d %H:%M:%S %Z')} | {issue.stage.value} | {issue.error_code}",
                f"  user: {user_part}",
                f"  provider: {provider_part} / {model_part}",
                f"  details: {_truncate_issue_field(issue.technical_message)}",
                f"  text: {_truncate_issue_field(issue.request_text)}",
                f"  payload: {_truncate_issue_field(issue.raw_payload)}",
            ]
        )
    return "\n".join(lines)


def load_admin_llm_issues_page(
    *,
    total_count: int,
    page: int,
    page_size: int = ADMIN_LLM_ISSUE_PAGE_SIZE,
) -> tuple[int, int, bool, bool]:
    page = max(page, 0)
    if total_count == 0:
        return 0, 0, False, False

    max_page = max((total_count - 1) // page_size, 0)
    page = min(page, max_page)
    offset = page * page_size
    has_previous_page = page > 0
    has_next_page = offset + page_size < total_count
    return page, offset, has_previous_page, has_next_page


def _truncate_issue_field(value: str | None, *, max_length: int = 160) -> str:
    if value is None or not value.strip():
        return "—"
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 1] + "…"


def log_llm_issue(
    *,
    session_factory: sessionmaker[Session],
    stage: LLMIssueStage,
    error_code: str | None,
    provider: str | None,
    model: str | None,
    telegram_user_id: int | None,
    username: str | None,
    request_text: str | None,
    raw_payload: str | None,
    technical_message: str | None,
) -> None:
    with session_scope(session_factory) as session:
        LLMIssueLogRepository(session).create(
            LLMIssueLogCreate(
                stage=stage,
                error_code=error_code or "unknown_error",
                provider=provider,
                model=model,
                telegram_user_id=telegram_user_id,
                username=username,
                request_text=request_text,
                raw_payload=raw_payload,
                technical_message=technical_message,
            )
        )


def build_extracted_workout_metric_lines(payload) -> list[str]:
    lines: list[str] = []
    for entry in payload.entries:
        if entry.type is not EntryType.WORKOUT:
            continue
        for item in entry.items:
            for metric in item.metrics:
                if metric.code == "workout_calories":
                    lines.append(f"- калории тренировки: {round(metric.value, 1)} ккал")
                    lines.append(
                        f"- к компенсации питания: {round(calculate_default_workout_calorie_credit(metric.value), 1)} ккал"
                    )
    return lines


def build_workout_entries_report(entries: list, *, timezone_name: str) -> str | None:
    if not entries:
        return None

    lines = ["Тренировки:"]
    for entry in entries:
        lines.append(f"- {format_entry_timestamp(entry, timezone_name)} — {build_workout_entry_title(entry)}")
    return "\n".join(lines)


def build_write_confirmation_response(
    saved_items: list[EntryItemCreate],
    extra_lines: list[str] | None = None,
    day_report: str | None = None,
    coach_comment: str | None = None,
) -> str:
    saved_items_confirmation = build_saved_items_confirmation(saved_items)
    if extra_lines:
        saved_items_confirmation = "\n".join([saved_items_confirmation, *extra_lines])
    parts = [saved_items_confirmation]
    if day_report is not None:
        parts.append(day_report)
    if coach_comment:
        parts.append(f"{NUTRITION_COACH_DISPLAY_NAME}: {coach_comment}")
    return "\n\n".join(parts)


def format_entry_timestamp(entry, timezone_name: str) -> str:
    occurred_at = entry.occurred_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return occurred_at.astimezone(ZoneInfo(timezone_name)).strftime("%H:%M")


def build_recent_entry_title(entry) -> str:
    item_texts = [
        format_saved_item_line(item.name, item.quantity, item.unit).removeprefix("- ")
        for item in sorted(entry.items, key=lambda current: current.position)
    ]
    if item_texts:
        return ", ".join(item_texts)
    return "запись без позиций"


def build_workout_entry_title(entry) -> str:
    base_title = build_recent_entry_title(entry)
    workout_calories = resolve_workout_metric_value(entry, "workout_calories")
    workout_credit = resolve_workout_metric_value(entry, "workout_calorie_credit")
    if workout_calories <= 0 and workout_credit <= 0:
        return base_title
    details: list[str] = []
    if workout_calories > 0:
        details.append(f"{round(workout_calories, 1)} ккал")
    if workout_credit > 0:
        details.append(f"компенсация {round(workout_credit, 1)} ккал")
    if base_title.endswith(")"):
        return base_title[:-1] + f", {', '.join(details)})"
    return f"{base_title} ({', '.join(details)})"


def truncate_button_label(value: str, *, max_length: int = 28) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1].rstrip() + "…"


def build_recent_entry_button_label(entry, timezone_name: str) -> str:
    return truncate_button_label(
        f"{format_entry_timestamp(entry, timezone_name)} · {build_recent_entry_title(entry)}"
    )


def build_recent_entry_display_line(*, index: int, entry, timezone_name: str) -> str:
    title = truncate_button_label(build_recent_entry_title(entry), max_length=RECENT_ENTRY_LIST_TITLE_MAX_LENGTH)
    return f"{index}. {format_entry_timestamp(entry, timezone_name)} — {title}"


def build_recent_entries_day_heading(*, entry, timezone_name: str, nutrition_day_start_hour: int) -> str:
    summary_date = resolve_local_summary_date(
        reference_at=entry.occurred_at,
        timezone_name=timezone_name,
        nutrition_day_start_hour=nutrition_day_start_hour,
    )
    return summary_date.strftime("%d.%m.%Y")


def build_recent_entries_response(
    entries: list,
    *,
    timezone_name: str,
    page: int,
    count: int,
    nutrition_day_start_hour: int,
    selection_mode: bool = False,
) -> str:
    if not entries:
        return "Пока записей нет. Отправь еду текстом, фото блюда или нажми кнопку воды."

    lines = [f"Последние записи (страница {page + 1}, по {count}):"]
    current_heading: str | None = None
    for index, entry in enumerate(entries, start=page * count + 1):
        heading = build_recent_entries_day_heading(
            entry=entry,
            timezone_name=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
        )
        if heading != current_heading:
            lines.extend(["", heading])
            current_heading = heading
        lines.append(build_recent_entry_display_line(index=index, entry=entry, timezone_name=timezone_name))
    if selection_mode:
        lines.extend(["", "Выбери запись, которую нужно удалить."])
    return "\n".join(lines)


def build_recent_entry_delete_confirmation(*, entry, timezone_name: str) -> str:
    return "\n".join(
        [
            "Удалить эту запись?",
            "",
            f"{format_entry_timestamp(entry, timezone_name)} — {build_recent_entry_title(entry)}",
            "",
            "Это действие необратимо. Если запись понадобится снова, её нужно будет создать заново.",
        ]
    )


def render_goal_status_prefix(*, metric_value: float, goal_value: float | None, tolerance_percent: int) -> str:
    if goal_value is None or goal_value <= 0:
        return GOAL_STATUS_BELOW_EMOJI
    tolerance_delta = goal_value * tolerance_percent / 100
    lower_bound = goal_value - tolerance_delta
    upper_bound = goal_value + tolerance_delta
    if metric_value < lower_bound:
        return GOAL_STATUS_BELOW_EMOJI
    if metric_value > upper_bound:
        return GOAL_STATUS_ABOVE_EMOJI
    return GOAL_STATUS_WITHIN_EMOJI


def build_today_summary_response(summary: DailyNutritionSummary) -> str:
    if summary.included_entry_count == 0 and summary.excluded_entry_count == 0:
        return "За текущий день пока нет записей еды."

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
    force_render_summary: bool = False,
) -> str:
    if (
        summary.included_entry_count == 0
        and summary.excluded_entry_count == 0
        and (water_summary is None or (
            water_summary.included_entry_count == 0 and water_summary.excluded_entry_count == 0
        ))
        and not force_render_summary
    ):
        return "За текущий день пока нет записей. Отправь еду, фото блюда или воду."
    if not enabled_metric_codes:
        return "В summary сейчас всё скрыто. Включи хотя бы один показатель в /settings."
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
    workout_logging_enabled: bool,
    show_calories: bool,
    show_protein: bool,
    show_fat: bool,
    show_carbs: bool,
    show_fiber: bool,
    show_water: bool,
    show_post_entry_delta_suffix: bool,
    summary_display_mode: str,
    nutrition_day_start_hour: int,
    report_goal_tolerance_percent: int,
    report_noticeable_entry_percentile: int,
) -> str:
    statuses = {
        True: "включено",
        False: "выключено",
    }
    return "\n".join(
        [
            "Настройки summary:",
            f"- тренировки: {statuses[workout_logging_enabled]}",
            f"- калории: {statuses[show_calories]}",
            f"- белки: {statuses[show_protein]}",
            f"- жиры: {statuses[show_fat]}",
            f"- углеводы: {statuses[show_carbs]}",
            f"- клетчатка: {statuses[show_fiber]}",
            f"- вода: {statuses[show_water]}",
            f"- дельта записи: {statuses[show_post_entry_delta_suffix]}",
            f"- отображение: {SUMMARY_DISPLAY_MODE_LABELS[summary_display_mode]}",
            f"- начало дня: {nutrition_day_start_hour:02d}:00",
            f"- допуск к цели: {report_goal_tolerance_percent}%",
            f"- порог заметных записей: {report_noticeable_entry_percentile}%",
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


def payload_contains_workout_entries(payload) -> bool:
    return any(entry.type is EntryType.WORKOUT for entry in payload.entries)


def payload_contains_food_or_water_entries(payload) -> bool:
    return any(entry.type in {EntryType.FOOD, EntryType.WATER} for entry in payload.entries)


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
    workout_logging_enabled: bool,
    summary_preference,
    metric_deltas: dict[str, float] | None = None,
) -> str:
    workout_entries = []
    workout_calorie_credit_total = 0
    if workout_logging_enabled:
        occurred_at_from, occurred_at_to = resolve_day_bounds_utc(
            summary_date=summary_date,
            timezone_name=timezone_name,
            nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
        )
        workout_entries = EntryRepository(session).list_workout_for_user_between(
            user_id=user_id,
            occurred_at_from=occurred_at_from,
            occurred_at_to=occurred_at_to,
        )
        workout_calorie_credit_total = int(
            round(
                DailyWorkoutCalorieCreditUseCase(session).run(
                    user_id=user_id,
                    timezone_name=timezone_name,
                    summary_date=summary_date,
                    nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
                )
            )
        )
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
        calorie_goal_adjustment=workout_calorie_credit_total,
    )
    summary_report = build_today_summary_response_with_preferences(
        summary,
        enabled_metric_codes=get_enabled_summary_metric_codes(summary_preference),
        summary_display_mode=summary_preference.summary_display_mode,
        goal_progress=goal_progress,
        water_summary=water_summary,
        metric_deltas=metric_deltas,
        show_post_entry_delta_suffix=summary_preference.show_post_entry_delta_suffix,
        force_render_summary=workout_calorie_credit_total > 0,
    )
    if not workout_logging_enabled:
        return summary_report
    workout_report = build_workout_entries_report(workout_entries, timezone_name=timezone_name)
    if workout_report is None:
        return summary_report

    has_nutrition_or_water_entries = not (
        summary.included_entry_count == 0
        and summary.excluded_entry_count == 0
        and water_summary.included_entry_count == 0
        and water_summary.excluded_entry_count == 0
    )
    if not has_nutrition_or_water_entries and workout_calorie_credit_total <= 0:
        return workout_report

    return "\n\n".join([summary_report, workout_report])


def build_period_report_response(
    report,
    *,
    enabled_metric_codes: tuple[str, ...],
    workout_logging_enabled: bool,
    report_goal_tolerance_percent: int,
) -> str:
    show_nutrition_metrics = any(metric_code != "water" for metric_code in enabled_metric_codes)
    show_water = "water" in enabled_metric_codes
    has_any_metric_to_render = show_nutrition_metrics or show_water
    has_any_workout_data = workout_logging_enabled and report.workout_entry_count > 0
    if not has_any_metric_to_render and not has_any_workout_data:
        return "В summary сейчас всё скрыто. Включи хотя бы один показатель в /settings."

    has_visible_data = (
        (show_nutrition_metrics and report.food_data_day_count > 0)
        or (show_water and report.water_data_day_count > 0)
        or has_any_workout_data
    )
    if not has_visible_data:
        return "За этот период пока нет данных по включённым показателям."

    lines = [
        f"Отчёт: {report.summary_date_from.strftime('%d.%m.%Y')}-{report.summary_date_to.strftime('%d.%m.%Y')}",
        f"Дней в периоде: {report.period_day_count}",
    ]
    if show_nutrition_metrics:
        lines.append(f"Дней с данными по еде: {report.food_data_day_count}")
    if show_water:
        lines.append(f"Дней с данными по воде: {report.water_data_day_count}")
    if has_any_workout_data:
        lines.append(f"Дней с тренировками: {report.workout_day_count}")

    average_lines: list[str] = []
    if show_nutrition_metrics and report.food_data_day_count > 0:
        for metric_code, _short_label, unit in SUMMARY_METRIC_LINES:
            if metric_code not in enabled_metric_codes or metric_code == "water":
                continue
            metric_value = getattr(report.average_nutrition_totals, metric_code)
            goal_value = report.average_goal_values.get(metric_code)
            prefix = render_goal_status_prefix(
                metric_value=metric_value,
                goal_value=goal_value,
                tolerance_percent=report_goal_tolerance_percent,
            )
            average_lines.append(f"{prefix} {GOAL_METRIC_LABELS[metric_code]}: {round(metric_value, 1)} {unit}")
    if show_water and report.water_data_day_count > 0:
        prefix = render_goal_status_prefix(
            metric_value=report.average_water_ml,
            goal_value=report.average_goal_values.get("water"),
            tolerance_percent=report_goal_tolerance_percent,
        )
        average_lines.append(f"{prefix} вода: {round(report.average_water_ml, 1)} мл")

    if average_lines:
        lines.extend(["", "Среднее по дням с данными", *average_lines])

    goal_lines: list[str] = []
    for metric_code in enabled_metric_codes:
        applicable_day_count = report.goal_applicable_day_counts.get(metric_code, 0)
        if applicable_day_count <= 0:
            continue
        hit_day_count = report.goal_hit_day_counts.get(metric_code, 0)
        goal_lines.append(f"- {GOAL_METRIC_LABELS[metric_code]}: {hit_day_count} из {applicable_day_count} дней")
    if goal_lines:
        lines.extend(["", f"Цели считаются с допуском {report_goal_tolerance_percent}%.", *goal_lines])

    if has_any_workout_data:
        lines.extend(
            [
                "",
                "Тренировки",
                f"- тренировок: {report.workout_entry_count}",
            ]
        )

    notes: list[str] = []
    if show_nutrition_metrics and report.incomplete_food_day_count > 0:
        notes.append(f"Неполных дней по еде: {report.incomplete_food_day_count}.")
    if show_water and report.incomplete_water_day_count > 0:
        notes.append(f"Неполных дней по воде: {report.incomplete_water_day_count}.")
    if notes:
        lines.extend(["", *notes])

    return "\n".join(lines)


def build_period_report(
    *,
    session: Session,
    user_id: int,
    timezone_name: str,
    summary_date_to: date,
    period_days: int,
    workout_logging_enabled: bool,
    summary_preference,
) -> str:
    report = PeriodReportUseCase(session).run(
        user_id=user_id,
        timezone_name=timezone_name,
        summary_date_to=summary_date_to,
        period_day_count=period_days,
        nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
        workout_logging_enabled=workout_logging_enabled,
        report_goal_tolerance_percent=summary_preference.report_goal_tolerance_percent,
    )
    return build_period_report_response(
        report,
        enabled_metric_codes=get_enabled_summary_metric_codes(summary_preference),
        workout_logging_enabled=workout_logging_enabled,
        report_goal_tolerance_percent=summary_preference.report_goal_tolerance_percent,
    )


def resolve_next_period_days(period_days: int) -> int:
    try:
        current_index = PERIOD_REPORT_PERIOD_SEQUENCE.index(period_days)
    except ValueError:
        return DEFAULT_PERIOD_REPORT_DAYS
    return PERIOD_REPORT_PERIOD_SEQUENCE[(current_index + 1) % len(PERIOD_REPORT_PERIOD_SEQUENCE)]


def resolve_available_period_report_metric_codes(
    *,
    session: Session,
    user_id: int,
    timezone_name: str,
    summary_date_to: date,
    period_days: int,
    summary_preference,
    workout_logging_enabled: bool,
) -> tuple[str, ...]:
    enabled_metric_codes = get_enabled_summary_metric_codes(summary_preference)
    report_use_case = PeriodReportUseCase(session)
    available_metric_codes: list[str] = []
    for metric_code in enabled_metric_codes:
        dynamics = report_use_case.build_metric_dynamics(
            user_id=user_id,
            timezone_name=timezone_name,
            summary_date_to=summary_date_to,
            period_day_count=period_days,
            metric_code=metric_code,
            nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
            subperiod_day_count=PERIOD_REPORT_SUBPERIOD_DAYS,
            workout_logging_enabled=workout_logging_enabled,
        )
        if any(row.data_day_count > 0 for row in dynamics.rows):
            available_metric_codes.append(metric_code)
    return tuple(available_metric_codes)


def resolve_next_metric_code(metric_codes: tuple[str, ...], current_metric_code: str) -> str:
    if not metric_codes:
        raise ValueError("metric_codes must not be empty")
    try:
        current_index = metric_codes.index(current_metric_code)
    except ValueError:
        return metric_codes[0]
    return metric_codes[(current_index + 1) % len(metric_codes)]


def build_period_report_dynamics_response(
    dynamics: PeriodMetricDynamics,
    *,
    available_metric_codes: tuple[str, ...],
    report_goal_tolerance_percent: int,
) -> str:
    unit_by_metric_code = {metric_code: unit for metric_code, _short_label, unit in SUMMARY_METRIC_LINES}
    metric_labels = [f"[{GOAL_METRIC_LABELS[metric_code]}]" if metric_code == dynamics.metric_code else GOAL_METRIC_LABELS[metric_code] for metric_code in available_metric_codes]
    lines = [
        f"Динамика: {dynamics.summary_date_from.strftime('%d.%m.%Y')}-{dynamics.summary_date_to.strftime('%d.%m.%Y')}",
        f"Метрика: {GOAL_METRIC_LABELS[dynamics.metric_code]}",
        f"Доступно: {', '.join(metric_labels)}",
        "",
    ]
    unit = unit_by_metric_code[dynamics.metric_code]
    for row in dynamics.rows:
        period_label = f"{row.summary_date_from.strftime('%d.%m')}-{row.summary_date_to.strftime('%d.%m')}"
        if row.data_day_count == 0 or row.average_value is None:
            lines.append(f"{period_label}: нет данных")
            continue
        prefix = render_goal_status_prefix(
            metric_value=row.average_value,
            goal_value=row.average_goal_value,
            tolerance_percent=report_goal_tolerance_percent,
        )
        lines.append(
            f"{prefix} {period_label}: {round(row.average_value, 1)} {unit} ({row.data_day_count}/{dynamics.subperiod_day_count} дней)"
        )
    return "\n".join(lines)


def build_period_report_noticeable_response(
    noticeable_entries,
    *,
    available_metric_codes: tuple[str, ...],
) -> str:
    unit_by_metric_code = {metric_code: unit for metric_code, _short_label, unit in SUMMARY_METRIC_LINES}
    metric_labels = [
        f"[{GOAL_METRIC_LABELS[metric_code]}]" if metric_code == noticeable_entries.metric_code else GOAL_METRIC_LABELS[metric_code]
        for metric_code in available_metric_codes
    ]
    lines = [
        f"Заметные записи пищи: {noticeable_entries.summary_date_from.strftime('%d.%m.%Y')}-{noticeable_entries.summary_date_to.strftime('%d.%m.%Y')}",
        f"Метрика: {GOAL_METRIC_LABELS[noticeable_entries.metric_code]}",
        f"Доступно: {', '.join(metric_labels)}",
        "",
    ]
    unit = unit_by_metric_code[noticeable_entries.metric_code]
    if not noticeable_entries.entries:
        lines.append("Нет заметных записей.")
        return "\n".join(lines)
    for entry in noticeable_entries.entries:
        lines.append(
            f"- {entry.occurred_at.strftime('%d.%m')} · {entry.title} · {round(entry.metric_value, 1)} {unit}"
        )
    return "\n".join(lines)


def payload_contains_credit_eligible_workout_entries(payload) -> bool:
    for entry in payload.entries:
        if entry.type is not EntryType.WORKOUT:
            continue
        for item in entry.items:
            if any(metric.code == "workout_calories" for metric in item.metrics):
                return True
    return False


def resolve_recent_entry_for_callback(
    *,
    entry_repository: EntryRepository,
    user_id: int,
    entry_id: int,
):
    return entry_repository.get_by_id_for_user(entry_id=entry_id, user_id=user_id)


def load_recent_entries_page(
    *,
    entry_repository: EntryRepository,
    user_id: int,
    page: int,
    page_size: int = RECENT_ENTRIES_DEFAULT_COUNT,
) -> tuple[int, list, bool, bool]:
    page = max(page, 0)
    while True:
        entries = entry_repository.list_recent_for_user(
            user_id=user_id,
            limit=page_size + 1,
            offset=page * page_size,
        )
        if entries or page == 0:
            page_entries = entries[:page_size]
            return page, page_entries, page > 0, len(entries) > page_size
        page -= 1


def resolve_recent_count(command: CommandObject | None) -> int | None:
    parsed_value = parse_positive_int_arg(command)
    if parsed_value is None:
        return None
    return min(parsed_value, RECENT_ENTRIES_MAX_COUNT)


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


def is_photo_media_group_message(message: Message) -> bool:
    return bool(getattr(message, "photo", None) and getattr(message, "media_group_id", None))


async def send_typing_action(message: Message) -> None:
    bot = getattr(message, "bot", None)
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if bot is None or chat_id is None:
        return

    send_chat_action = getattr(bot, "send_chat_action", None)
    if send_chat_action is None:
        return

    await send_chat_action(chat_id=chat_id, action="typing")


async def _typing_action_loop(*, message: Message, interval_seconds: float = 4.0) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await send_typing_action(message)


async def start_typing_indicator(message: Message) -> asyncio.Task | None:
    bot = getattr(message, "bot", None)
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if bot is None or chat_id is None:
        return None

    await send_typing_action(message)
    return asyncio.create_task(_typing_action_loop(message=message))


@router.message(Command("admin"))
async def handle_admin(
    message: Message,
    session_factory: sessionmaker[Session],
    backfill_tracker: AdminBackfillTracker,
    admin_user_ids: tuple[int, ...] = (),
    app_version: str = "unknown",
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
        issue_repository = LLMIssueLogRepository(session)
        recent_issue_since = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_extraction_issue_count = issue_repository.count_recent_by_stage(
            stage=LLMIssueStage.EXTRACTION,
            since=recent_issue_since,
        )
        recent_nutrition_issue_count = issue_repository.count_recent_by_stage(
            stage=LLMIssueStage.NUTRITION,
            since=recent_issue_since,
        )

    await message.answer(
        build_admin_overview_response(
            admin_user_id=telegram_user.id,
            admin_user_ids=admin_user_ids,
            known_users=known_users,
            incomplete_food_entry_count=incomplete_food_entry_count,
            recent_extraction_issue_count=recent_extraction_issue_count,
            recent_nutrition_issue_count=recent_nutrition_issue_count,
            backfill_status_line=build_admin_backfill_status_line(backfill_tracker),
            app_version=app_version,
        ),
        reply_markup=build_admin_overview_keyboard(),
    )


@router.callback_query(AdminPanelCallback.filter())
async def handle_admin_panel_callback(
    callback: CallbackQuery,
    callback_data: AdminPanelCallback,
    session_factory: sessionmaker[Session],
    backfill_tracker: AdminBackfillTracker,
    admin_user_ids: tuple[int, ...] = (),
    app_version: str = "unknown",
) -> None:
    telegram_user = callback.from_user
    if telegram_user is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    if not is_admin_user(telegram_user.id, admin_user_ids):
        await callback.answer("Команда доступна только администратору.", show_alert=True)
        return

    if callback_data.action == "close":
        await safe_delete_message(callback.message)
        await callback.answer()
        return

    with session_scope(session_factory) as session:
        known_users = UserAccessRepository(session).list_known_users()
        directory_users = build_admin_user_directory(known_users, admin_user_ids)

        if callback_data.action == "overview":
            incomplete_food_entry_count = EntryRepository(session).count_incomplete_food_entries(
                required_metric_codes=list(SUPPORTED_NUTRITION_METRIC_CODES)
            )
            issue_repository = LLMIssueLogRepository(session)
            recent_issue_since = datetime.now(timezone.utc) - timedelta(hours=24)
            await safe_edit_message_text(
                callback.message,
                text=build_admin_overview_response(
                    admin_user_id=telegram_user.id,
                    admin_user_ids=admin_user_ids,
                    known_users=known_users,
                    incomplete_food_entry_count=incomplete_food_entry_count,
                    recent_extraction_issue_count=issue_repository.count_recent_by_stage(
                        stage=LLMIssueStage.EXTRACTION,
                        since=recent_issue_since,
                    ),
                    recent_nutrition_issue_count=issue_repository.count_recent_by_stage(
                        stage=LLMIssueStage.NUTRITION,
                        since=recent_issue_since,
                    ),
                    backfill_status_line=build_admin_backfill_status_line(backfill_tracker),
                    app_version=app_version,
                ),
                reply_markup=build_admin_overview_keyboard(),
            )
            await callback.answer()
            return

        if callback_data.action == "open_users":
            page, page_users, has_previous_page, has_next_page = load_admin_known_users_page(
                known_users=directory_users,
                page=callback_data.page,
                page_size=ADMIN_USER_PAGE_SIZE,
            )
            await safe_edit_message_text(
                callback.message,
                text=build_admin_users_page_response(
                    page_users,
                    page=page,
                    page_size=ADMIN_USER_PAGE_SIZE,
                    admin_user_ids=admin_user_ids,
                ),
                reply_markup=build_admin_user_list_keyboard(
                    user_buttons=[
                        (
                            build_admin_user_button_label(
                                known_user,
                                is_admin=known_user.telegram_user_id in admin_user_ids,
                            ),
                            known_user.telegram_user_id,
                        )
                        for known_user in page_users
                    ],
                    page=page,
                    has_previous_page=has_previous_page,
                    has_next_page=has_next_page,
                ),
            )
            await callback.answer()
            return

        if callback_data.action == "open_llm_issues":
            issue_repository = LLMIssueLogRepository(session)
            total_count = issue_repository.count_recent_by_stage(
                stage=LLMIssueStage.EXTRACTION,
                since=datetime(1970, 1, 1, tzinfo=timezone.utc),
            ) + issue_repository.count_recent_by_stage(
                stage=LLMIssueStage.NUTRITION,
                since=datetime(1970, 1, 1, tzinfo=timezone.utc),
            )
            page, offset, has_previous_page, has_next_page = load_admin_llm_issues_page(
                total_count=total_count,
                page=callback_data.page,
                page_size=ADMIN_LLM_ISSUE_PAGE_SIZE,
            )
            issues = issue_repository.list_recent(limit=ADMIN_LLM_ISSUE_PAGE_SIZE, offset=offset)
            await safe_edit_message_text(
                callback.message,
                text=build_llm_issue_log_summary(
                    page=page,
                    page_size=ADMIN_LLM_ISSUE_PAGE_SIZE,
                    issues=issues,
                ),
                reply_markup=build_admin_llm_issues_keyboard(
                    page=page,
                    has_previous_page=has_previous_page,
                    has_next_page=has_next_page,
                ),
            )
            await callback.answer()
            return

        known_user = find_known_user(directory_users, telegram_user_id=callback_data.telegram_user_id)
        if known_user is None:
            await callback.answer("Пользователь уже недоступен.", show_alert=True)
            return
        is_admin_target = known_user.telegram_user_id in admin_user_ids

        if callback_data.action == "allow_user":
            if is_admin_target:
                await callback.answer("Для администратора это действие недоступно.", show_alert=True)
                return
            updated_access = UserAccessRepository(session).set_access(
                telegram_user_id=known_user.telegram_user_id,
                username=known_user.username,
                is_allowed=True,
            )
            known_user = find_known_user(
                build_admin_user_directory(UserAccessRepository(session).list_known_users(), admin_user_ids),
                telegram_user_id=callback_data.telegram_user_id,
            )
            entry_owner = UserRepository(session).get_by_telegram_user_id(callback_data.telegram_user_id)
            entry_count = 0
            if entry_owner is not None:
                entry_count = len(EntryRepository(session).list_recent_for_user(user_id=entry_owner.id, limit=100000))
            await safe_edit_message_text(
                callback.message,
                text=build_admin_user_actions_response(
                    known_user=known_user,
                    entry_count=entry_count,
                    is_admin=False,
                ),
                reply_markup=build_admin_user_actions_keyboard(
                    telegram_user_id=updated_access.telegram_user_id,
                    page=callback_data.page,
                    is_allowed=True,
                    is_admin=False,
                ),
            )
            await callback.answer("Доступ разрешён.")
            return

        if callback_data.action == "deny_user":
            if is_admin_target:
                await callback.answer("Для администратора это действие недоступно.", show_alert=True)
                return
            updated_access = UserAccessRepository(session).set_access(
                telegram_user_id=known_user.telegram_user_id,
                username=known_user.username,
                is_allowed=False,
            )
            known_user = find_known_user(
                build_admin_user_directory(UserAccessRepository(session).list_known_users(), admin_user_ids),
                telegram_user_id=callback_data.telegram_user_id,
            )
            entry_owner = UserRepository(session).get_by_telegram_user_id(callback_data.telegram_user_id)
            entry_count = 0
            if entry_owner is not None:
                entry_count = len(EntryRepository(session).list_recent_for_user(user_id=entry_owner.id, limit=100000))
            await safe_edit_message_text(
                callback.message,
                text=build_admin_user_actions_response(
                    known_user=known_user,
                    entry_count=entry_count,
                    is_admin=False,
                ),
                reply_markup=build_admin_user_actions_keyboard(
                    telegram_user_id=updated_access.telegram_user_id,
                    page=callback_data.page,
                    is_allowed=False,
                    is_admin=False,
                ),
            )
            await callback.answer("Доступ запрещён.")
            return

        entry_owner = UserRepository(session).get_by_telegram_user_id(callback_data.telegram_user_id)
        entry_count = 0
        if entry_owner is not None:
            entry_count = len(EntryRepository(session).list_recent_for_user(user_id=entry_owner.id, limit=100000))

        if callback_data.action == "open_user":
            await safe_edit_message_text(
                callback.message,
                text=build_admin_user_actions_response(
                    known_user=known_user,
                    entry_count=entry_count,
                    is_admin=is_admin_target,
                ),
                reply_markup=build_admin_user_actions_keyboard(
                    telegram_user_id=known_user.telegram_user_id,
                    page=callback_data.page,
                    is_allowed=known_user.is_allowed,
                    is_admin=is_admin_target,
                ),
            )
            await callback.answer()
            return

        if callback_data.action == "prompt_delete_user_entries":
            await safe_edit_message_text(
                callback.message,
                text=build_admin_delete_entries_prompt(
                    telegram_user_id=known_user.telegram_user_id,
                    entry_count=entry_count,
                ),
                reply_markup=build_admin_delete_entries_confirmation_keyboard(
                    telegram_user_id=known_user.telegram_user_id,
                    page=callback_data.page,
                ),
            )
            await callback.answer()
            return

        if callback_data.action != "confirm_delete_user_entries":
            await callback.answer("Неизвестное действие.", show_alert=True)
            return

        if entry_owner is None:
            await safe_edit_message_text(
                callback.message,
                text=f"Удалено записей пользователя {callback_data.telegram_user_id}: 0.",
                reply_markup=None,
            )
            await callback.answer("Удаление выполнено.")
            return

        deleted_count = EntryRepository(session).delete_all_for_user(user_id=entry_owner.id)

    await safe_edit_message_text(
        callback.message,
        text=f"Удалено записей пользователя {callback_data.telegram_user_id}: {deleted_count}.",
        reply_markup=None,
    )
    await callback.answer("Удаление выполнено.")


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
        await message.answer("Дозаполнение nutrition metrics уже выполняется.")
        return

    await message.answer(f"Запускаю дозаполнение nutrition metrics. Лимит: {limit}.")

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
            "Профиль создан.\n"
            "Что можно сделать:\n"
            "- отправить запись еды текстом или фото блюда;\n"
            "- нажать кнопку воды;\n"
            "- при желании включить запись тренировок в /settings;\n"
            "- задать вопрос о питании;\n"
            "- посмотреть итог дня: /today;\n"
            "- посмотреть отчёт за период: /report;\n"
            "- посмотреть и удалить последние записи: /recent;\n"
            "- посмотреть или изменить цели: /goal;\n"
            "- настроить summary: /settings;\n"
            "- управлять файлами импорта и экспорта: /files.",
            reply_markup=build_main_keyboard(),
        )
        return

    await message.answer(
        "Бот готов.\n"
        "Что можно сделать:\n"
        "- отправить запись еды текстом или фото блюда;\n"
        "- нажать кнопку воды;\n"
        "- при желании включить запись тренировок в /settings;\n"
        "- задать вопрос о питании;\n"
        "- посмотреть итог дня: /today;\n"
        "- посмотреть отчёт за период: /report;\n"
        "- посмотреть и удалить последние записи: /recent;\n"
        "- посмотреть или изменить цели: /goal;\n"
        "- настроить summary: /settings;\n"
        "- управлять файлами импорта и экспорта: /files.",
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


@router.message(Command("provider"))
async def handle_provider(
    message: Message,
    session_factory: sessionmaker[Session],
    settings: Settings | None = None,
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    if settings is None:
        await message.answer("Настройки провайдеров в этом окружении недоступны.")
        return

    telegram_user = message.from_user
    if telegram_user is None:
        raise ValueError("Incoming message does not contain Telegram user")

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        access = UserAccessRepository(session).get_by_telegram_user_id(telegram_user.id)
        profile, _created = UserLLMProfileRepository(session).get_or_create(user_id=user_id)
        connections = UserLLMConnectionRepository(session).list_views_for_user(user_id=user_id)

    project_openai_enabled = bool(
        settings.enable_openai_provider
        and settings.openai_api_key
        and (
            is_admin_user(telegram_user.id, admin_user_ids)
            or (
                access is not None
                and access.account_category is AccountCategory.INTERNAL
            )
        )
    )
    personal_openai_enabled = bool(
        settings.enable_openai_provider and settings.personal_api_keys_secret
    )
    await message.answer(
        build_provider_settings_response(
            telegram_user_id=telegram_user.id,
            account_category=(
                access.account_category.value if access is not None else AccountCategory.UNASSIGNED.value
            ),
            selection_mode=profile.selection_mode.value,
            project_openai_enabled=project_openai_enabled,
            personal_openai_enabled=personal_openai_enabled,
            connections=connections,
            admin_user_ids=admin_user_ids,
        ),
        reply_markup=build_main_keyboard(),
    )


@router.message(Command("provider_save_openai"))
async def handle_provider_save_openai(
    message: Message,
    command: CommandObject,
    session_factory: sessionmaker[Session],
    settings: Settings | None = None,
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    if settings is None:
        await message.answer("Настройки провайдеров в этом окружении недоступны.")
        return
    if not settings.enable_openai_provider:
        await message.answer("Класс провайдера OpenAI сейчас отключён в конфигурации приложения.")
        return
    if not settings.personal_api_keys_secret:
        await message.answer("В приложении не настроен секрет для хранения персональных API-ключей.")
        return

    parsed_args = parse_provider_save_command_args(command)
    if parsed_args is None:
        await message.answer(
            f"Использование: <code>/provider_save_openai {LLM_MODEL_PLACEHOLDER} {API_KEY_PLACEHOLDER}</code>"
        )
        return

    model, api_key = parsed_args
    cipher = SecretCipher(settings.personal_api_keys_secret)
    encrypted_api_key = cipher.encrypt(api_key)

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        connection_repository = UserLLMConnectionRepository(session)
        connection_repository.upsert_connection(
            user_id=user_id,
            provider=LLMProvider.OPENAI,
            model=model,
            encrypted_api_key=encrypted_api_key,
            is_enabled=True,
            is_selected=True,
            validation_status=LLMConnectionValidationStatus.UNKNOWN,
            validation_error=None,
        )
        connection_repository.select_provider(user_id=user_id, provider=LLMProvider.OPENAI)

    await message.answer(
        f"Сохранил персональный OpenAI-ключ для модели <code>{html.escape(model)}</code>.",
        reply_markup=build_main_keyboard(),
    )


@router.message(Command("provider_use_personal"))
async def handle_provider_use_personal(
    message: Message,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        connection_repository = UserLLMConnectionRepository(session)
        openai_connections = [
            connection
            for connection in connection_repository.list_for_user(user_id=user_id)
            if connection.provider is LLMProvider.OPENAI and connection.is_enabled
        ]
        if not openai_connections:
            await message.answer(
                "Сначала сохрани хотя бы один персональный OpenAI-ключ командой "
                f"<code>/provider_save_openai {LLM_MODEL_PLACEHOLDER} {API_KEY_PLACEHOLDER}</code>."
            )
            return
        connection_repository.select_provider(user_id=user_id, provider=LLMProvider.OPENAI)
        UserLLMProfileRepository(session).set_selection_mode(
            user_id=user_id,
            selection_mode=UserLLMSelectionMode.PERSONAL,
        )

    await message.answer(
        "Переключил LLM-сценарии на персональные OpenAI-ключи.",
        reply_markup=build_main_keyboard(),
    )


@router.message(Command("provider_use_project"))
async def handle_provider_use_project(
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
        access = UserAccessRepository(session).get_by_telegram_user_id(telegram_user.id)
        is_internal = is_admin_user(telegram_user.id, admin_user_ids) or (
            access is not None and access.account_category is AccountCategory.INTERNAL
        )
        if not is_internal:
            await message.answer("Проектный провайдер доступен только аккаунтам категории internal.")
            return

        UserLLMProfileRepository(session).set_selection_mode(
            user_id=user_id,
            selection_mode=UserLLMSelectionMode.PROJECT,
        )

    await message.answer(
        "Переключил LLM-сценарии на проектный провайдер.",
        reply_markup=build_main_keyboard(),
    )


@router.message(Command("recent"))
async def handle_recent(
    message: Message,
    command: CommandObject,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    raw_args = command.args.strip() if command.args is not None else ""
    if raw_args and parse_positive_int_arg(command) is None:
        await message.answer(
            f"Использование: <code>/recent [COUNT]</code>, где COUNT от 1 до {RECENT_ENTRIES_MAX_COUNT}."
        )
        return

    requested_count = parse_positive_int_arg(command)
    if requested_count is not None and requested_count > RECENT_ENTRIES_MAX_COUNT:
        await message.answer(
            f"Для <code>/recent</code> можно запросить от 1 до {RECENT_ENTRIES_MAX_COUNT} записей."
        )
        return

    recent_count = resolve_recent_count(command) or RECENT_ENTRIES_DEFAULT_COUNT

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        user = UserRepository(session).get_by_telegram_user_id(message.from_user.id)
        if user is None:
            raise RuntimeError("User profile was not found after registration")
        summary_preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user_id)
        page, entries, has_previous_page, has_next_page = load_recent_entries_page(
            entry_repository=EntryRepository(session),
            user_id=user_id,
            page=0,
            page_size=recent_count,
        )

    reply_markup = (
        build_recent_entries_delete_keyboard(
            page=page,
            count=recent_count,
            has_previous_page=has_previous_page,
            has_next_page=has_next_page,
        )
        if entries
        else build_main_keyboard()
    )
    await message.answer(
        build_recent_entries_response(
            entries,
            timezone_name=user.timezone,
            page=page,
            count=recent_count,
            nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
        ),
        reply_markup=reply_markup,
    )


@router.callback_query(RecentEntryDeleteCallback.filter())
async def handle_recent_delete_callback(
    callback: CallbackQuery,
    callback_data: RecentEntryDeleteCallback,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    telegram_user = callback.from_user
    if telegram_user is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    if callback_data.action == "close":
        await safe_delete_message(callback.message)
        await callback.answer()
        return

    with session_scope(session_factory) as session:
        if not (
            is_admin_user(telegram_user.id, admin_user_ids)
            or UserAccessRepository(session).is_allowed(telegram_user.id)
        ):
            await callback.answer("Нет доступа к боту. Попроси администратора его выдать.", show_alert=True)
            return

        user = UserRepository(session).get_by_telegram_user_id(telegram_user.id)
        if user is None:
            user, _created = UserRepository(session).get_or_create(
                telegram_user_id=telegram_user.id,
                username=telegram_user.username,
            )
        summary_preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user.id)
        entry_repository = EntryRepository(session)
        page, recent_entries, has_previous_page, has_next_page = load_recent_entries_page(
            entry_repository=entry_repository,
            user_id=user.id,
            page=callback_data.page,
            page_size=callback_data.count,
        )

        if callback_data.action == "list":
            await callback.message.edit_text(
                build_recent_entries_response(
                    recent_entries,
                    timezone_name=user.timezone,
                    page=page,
                    count=callback_data.count,
                    nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
                ),
                reply_markup=(
                    build_recent_entries_delete_keyboard(
                        page=page,
                        count=callback_data.count,
                        has_previous_page=has_previous_page,
                        has_next_page=has_next_page,
                    )
                    if recent_entries
                    else None
                ),
            )
            await callback.answer()
            return

        if callback_data.action == "open":
            await callback.message.edit_text(
                build_recent_entries_response(
                    recent_entries,
                    timezone_name=user.timezone,
                    page=page,
                    count=callback_data.count,
                    nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
                    selection_mode=True,
                ),
                reply_markup=(
                    build_recent_entry_selection_keyboard(
                        entry_buttons=[
                            (build_recent_entry_button_label(entry, user.timezone), entry.id)
                            for entry in recent_entries
                        ],
                        page=page,
                        count=callback_data.count,
                        has_previous_page=has_previous_page,
                        has_next_page=has_next_page,
                    )
                    if recent_entries
                    else None
                ),
            )
            await callback.answer()
            return

        selected_entry = resolve_recent_entry_for_callback(
            entry_repository=entry_repository,
            user_id=user.id,
            entry_id=callback_data.entry_id,
        )
        if selected_entry is None:
            await callback.answer("Эта запись уже удалена или больше недоступна.", show_alert=True)
            return

        if callback_data.action == "select":
            await callback.message.edit_text(
                build_recent_entry_delete_confirmation(entry=selected_entry, timezone_name=user.timezone),
                reply_markup=build_recent_entry_confirmation_keyboard(
                    entry_id=selected_entry.id,
                    page=page,
                    count=callback_data.count,
                ),
            )
            await callback.answer()
            return

        if callback_data.action != "confirm":
            await callback.answer("Неизвестное действие.", show_alert=True)
            return

        entry_repository.delete(selected_entry)
        page, updated_recent_entries, has_previous_page, has_next_page = load_recent_entries_page(
            entry_repository=entry_repository,
            user_id=user.id,
            page=page,
            page_size=callback_data.count,
        )

    await callback.message.edit_text(
        build_recent_entries_response(
            updated_recent_entries,
            timezone_name=user.timezone,
            page=page,
            count=callback_data.count,
            nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
        ),
        reply_markup=(
            build_recent_entries_delete_keyboard(
                page=page,
                count=callback_data.count,
                has_previous_page=has_previous_page,
                has_next_page=has_next_page,
            )
            if updated_recent_entries
            else None
        ),
    )
    await callback.answer("Запись удалена. Список уже обновлён.")


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
        user = UserRepository(session).get_by_telegram_user_id(message.from_user.id)
        if user is None:
            raise RuntimeError("User profile was not found after registration")
        preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user_id)

    await message.answer(
        build_summary_settings_response(
            workout_logging_enabled=user.workout_logging_enabled,
            show_calories=preference.show_calories,
            show_protein=preference.show_protein,
            show_fat=preference.show_fat,
            show_carbs=preference.show_carbs,
            show_fiber=preference.show_fiber,
            show_water=preference.show_water,
            show_post_entry_delta_suffix=preference.show_post_entry_delta_suffix,
            summary_display_mode=preference.summary_display_mode,
            nutrition_day_start_hour=preference.nutrition_day_start_hour,
            report_goal_tolerance_percent=preference.report_goal_tolerance_percent,
            report_noticeable_entry_percentile=preference.report_noticeable_entry_percentile,
        ),
        reply_markup=build_summary_settings_keyboard(
            workout_logging_enabled=user.workout_logging_enabled,
            show_calories=preference.show_calories,
            show_protein=preference.show_protein,
            show_fat=preference.show_fat,
            show_carbs=preference.show_carbs,
            show_fiber=preference.show_fiber,
            show_water=preference.show_water,
            show_post_entry_delta_suffix=preference.show_post_entry_delta_suffix,
            summary_display_mode=preference.summary_display_mode,
            nutrition_day_start_hour=preference.nutrition_day_start_hour,
            report_goal_tolerance_percent=preference.report_goal_tolerance_percent,
            report_noticeable_entry_percentile=preference.report_noticeable_entry_percentile,
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
        or callback_data.action == "cycle_report_goal_tolerance_percent"
        or callback_data.action == "cycle_report_noticeable_entry_percentile"
        or callback_data.action == "close"
    ):
        await callback.answer("Неизвестное действие.", show_alert=True)
        return
    if callback_data.action == "close":
        if callback.message is None:
            await callback.answer("Сообщение недоступно.", show_alert=True)
            return
        await safe_delete_message(callback.message)
        await callback.answer()
        return
    with session_scope(session_factory) as session:
        if not (
            is_admin_user(telegram_user.id, admin_user_ids)
            or UserAccessRepository(session).is_allowed(telegram_user.id)
        ):
            await callback.answer("Нет доступа к боту. Попроси администратора его выдать.", show_alert=True)
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
        elif callback_data.action == "cycle_report_goal_tolerance_percent":
            preference = preference_repository.cycle_report_goal_tolerance_percent(user_id=user.id)
        elif callback_data.action == "cycle_report_noticeable_entry_percentile":
            preference = preference_repository.cycle_report_noticeable_entry_percentile(user_id=user.id)
        elif callback_data.action == "toggle_workout_logging":
            user = UserRepository(session).toggle_workout_logging_enabled(user_id=user.id)
            preference, _created = preference_repository.get_or_create(user_id=user.id)
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
                workout_logging_enabled=user.workout_logging_enabled,
                show_calories=preference.show_calories,
                show_protein=preference.show_protein,
                show_fat=preference.show_fat,
                show_carbs=preference.show_carbs,
                show_fiber=preference.show_fiber,
                show_water=preference.show_water,
                show_post_entry_delta_suffix=preference.show_post_entry_delta_suffix,
                summary_display_mode=preference.summary_display_mode,
                nutrition_day_start_hour=preference.nutrition_day_start_hour,
                report_goal_tolerance_percent=preference.report_goal_tolerance_percent,
                report_noticeable_entry_percentile=preference.report_noticeable_entry_percentile,
            ),
            reply_markup=build_summary_settings_keyboard(
                workout_logging_enabled=user.workout_logging_enabled,
                show_calories=preference.show_calories,
                show_protein=preference.show_protein,
                show_fat=preference.show_fat,
                show_carbs=preference.show_carbs,
                show_fiber=preference.show_fiber,
                show_water=preference.show_water,
                show_post_entry_delta_suffix=preference.show_post_entry_delta_suffix,
                summary_display_mode=preference.summary_display_mode,
                nutrition_day_start_hour=preference.nutrition_day_start_hour,
                report_goal_tolerance_percent=preference.report_goal_tolerance_percent,
                report_noticeable_entry_percentile=preference.report_noticeable_entry_percentile,
            ),
        )
    await callback.answer("Сохранил настройки.")


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
            workout_logging_enabled=user.workout_logging_enabled,
            summary_preference=preference,
        )

    await message.answer(
        rendered_report,
        reply_markup=build_main_keyboard(),
    )


@router.message(Command("report"))
async def handle_report(
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

        summary_date_to = resolve_local_summary_date(
            reference_at=datetime.now(timezone.utc),
            timezone_name=user.timezone,
            nutrition_day_start_hour=preference.nutrition_day_start_hour,
        )
        rendered_report = build_period_report(
            session=session,
            user_id=user_id,
            timezone_name=user.timezone,
            summary_date_to=summary_date_to,
            period_days=DEFAULT_PERIOD_REPORT_DAYS,
            workout_logging_enabled=user.workout_logging_enabled,
            summary_preference=preference,
        )

    await message.answer(
        rendered_report,
        reply_markup=build_period_report_keyboard(period_days=DEFAULT_PERIOD_REPORT_DAYS),
    )


@router.callback_query(PeriodReportCallback.filter())
async def handle_period_report_callback(
    callback: CallbackQuery,
    callback_data: PeriodReportCallback,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    telegram_user = callback.from_user
    if telegram_user is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return
    if callback_data.action not in {
        "cycle_period",
        "open_dynamics",
        "cycle_dynamics_metric",
        "open_noticeable",
        "cycle_noticeable_metric",
        "close",
    }:
        await callback.answer("Неизвестное действие.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return

    if callback_data.action == "close":
        await safe_delete_message(callback.message)
        await callback.answer()
        return

    with session_scope(session_factory) as session:
        if not (
            is_admin_user(telegram_user.id, admin_user_ids)
            or UserAccessRepository(session).is_allowed(telegram_user.id)
        ):
            await callback.answer("Нет доступа к боту. Попроси администратора его выдать.", show_alert=True)
            return

        user = UserRepository(session).get_by_telegram_user_id(telegram_user.id)
        if user is None:
            user, _created = UserRepository(session).get_or_create(
                telegram_user_id=telegram_user.id,
                username=telegram_user.username,
            )
        preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user.id)

        summary_date_to = resolve_local_summary_date(
            reference_at=datetime.now(timezone.utc),
            timezone_name=user.timezone,
            nutrition_day_start_hour=preference.nutrition_day_start_hour,
        )
        if callback_data.action in {"open_dynamics", "cycle_dynamics_metric"}:
            available_metric_codes = resolve_available_period_report_metric_codes(
                session=session,
                user_id=user.id,
                timezone_name=user.timezone,
                summary_date_to=summary_date_to,
                period_days=callback_data.period_days,
                summary_preference=preference,
                workout_logging_enabled=user.workout_logging_enabled,
            )
            if not available_metric_codes:
                await callback.answer("За этот период нет данных для динамики.", show_alert=True)
                return
            metric_code = callback_data.metric_code or available_metric_codes[0]
            if metric_code not in available_metric_codes:
                metric_code = available_metric_codes[0]
            dynamics = PeriodReportUseCase(session).build_metric_dynamics(
                user_id=user.id,
                timezone_name=user.timezone,
                summary_date_to=summary_date_to,
                period_day_count=callback_data.period_days,
                metric_code=metric_code,
                nutrition_day_start_hour=preference.nutrition_day_start_hour,
                subperiod_day_count=PERIOD_REPORT_SUBPERIOD_DAYS,
                workout_logging_enabled=user.workout_logging_enabled,
            )
            next_metric_code = resolve_next_metric_code(available_metric_codes, metric_code)
            rendered_dynamics = build_period_report_dynamics_response(
                dynamics,
                available_metric_codes=available_metric_codes,
                report_goal_tolerance_percent=preference.report_goal_tolerance_percent,
            )
            reply_markup = build_period_report_dynamics_keyboard(
                period_days=callback_data.period_days,
                metric_code=metric_code,
                next_metric_code=next_metric_code,
            )
            if callback_data.action == "open_dynamics":
                await callback.message.answer(
                    rendered_dynamics,
                    reply_markup=reply_markup,
                )
                await callback.answer()
                return

            await callback.message.edit_text(
                rendered_dynamics,
                reply_markup=reply_markup,
            )
            await callback.answer("Метрика переключена.")
            return

        if callback_data.action in {"open_noticeable", "cycle_noticeable_metric"}:
            available_metric_codes = resolve_available_period_report_metric_codes(
                session=session,
                user_id=user.id,
                timezone_name=user.timezone,
                summary_date_to=summary_date_to,
                period_days=callback_data.period_days,
                summary_preference=preference,
                workout_logging_enabled=user.workout_logging_enabled,
            )
            if not available_metric_codes:
                await callback.answer("За этот период нет данных для заметных записей.", show_alert=True)
                return
            metric_code = callback_data.metric_code or available_metric_codes[0]
            if metric_code not in available_metric_codes:
                metric_code = available_metric_codes[0]
            noticeable_entries = PeriodReportUseCase(session).build_noticeable_entries(
                user_id=user.id,
                timezone_name=user.timezone,
                summary_date_to=summary_date_to,
                period_day_count=callback_data.period_days,
                metric_code=metric_code,
                nutrition_day_start_hour=preference.nutrition_day_start_hour,
                percentile=preference.report_noticeable_entry_percentile,
            )
            next_metric_code = resolve_next_metric_code(available_metric_codes, metric_code)
            rendered_noticeable = build_period_report_noticeable_response(
                noticeable_entries,
                available_metric_codes=available_metric_codes,
            )
            reply_markup = build_period_report_noticeable_keyboard(
                period_days=callback_data.period_days,
                metric_code=metric_code,
                next_metric_code=next_metric_code,
            )
            if callback_data.action == "open_noticeable":
                await callback.message.answer(
                    rendered_noticeable,
                    reply_markup=reply_markup,
                )
                await callback.answer()
                return

            await callback.message.edit_text(
                rendered_noticeable,
                reply_markup=reply_markup,
            )
            await callback.answer("Метрика переключена.")
            return

        next_period_days = resolve_next_period_days(callback_data.period_days)
        rendered_report = build_period_report(
            session=session,
            user_id=user.id,
            timezone_name=user.timezone,
            summary_date_to=summary_date_to,
            period_days=next_period_days,
            workout_logging_enabled=user.workout_logging_enabled,
            summary_preference=preference,
        )

    await callback.message.edit_text(
        rendered_report,
        reply_markup=build_period_report_keyboard(period_days=next_period_days),
    )
    await callback.answer("Период переключён.")


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
        reply_markup=build_goal_keyboard(),
    )


@router.callback_query(GoalMessageCallback.filter())
async def handle_goal_message_callback(
    callback: CallbackQuery,
    callback_data: GoalMessageCallback,
) -> None:
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    if callback_data.action != "close":
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    await safe_delete_message(callback.message)
    await callback.answer()


@router.message(Command("files"))
async def handle_files(
    message: Message,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    with session_scope(session_factory) as session:
        _, user_id = ensure_user_registered(message, session)
        files = DataExchangeService(session).list_files(user_id=user_id)

    await render_data_exchange_files_message(message, files=files)


@router.callback_query(DataExchangeFileCallback.filter())
async def handle_data_exchange_file_callback(
    callback: CallbackQuery,
    callback_data: DataExchangeFileCallback,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    telegram_user = callback.from_user
    if telegram_user is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    if callback_data.action == "close":
        await safe_delete_message(callback.message)
        await callback.answer()
        return

    with session_scope(session_factory) as session:
        if not (
            is_admin_user(telegram_user.id, admin_user_ids)
            or UserAccessRepository(session).is_allowed(telegram_user.id)
        ):
            await callback.answer("Нет доступа к боту. Попроси администратора его выдать.", show_alert=True)
            return

        user = UserRepository(session).get_by_telegram_user_id(telegram_user.id)
        if user is None:
            user, _created = UserRepository(session).get_or_create(
                telegram_user_id=telegram_user.id,
                username=telegram_user.username,
            )
        exchange_service = DataExchangeService(session)

        if callback_data.action == "refresh":
            files = exchange_service.list_files(user_id=user.id)
            await safe_edit_message_text(
                callback.message,
                text=build_data_exchange_files_response(files),
                reply_markup=build_data_exchange_files_keyboard(files=files),
            )
            await callback.answer()
            return

        if callback_data.action == "create_export":
            try:
                export_result = exchange_service.create_export_files(user=user)
            except FileLimitExceededError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            files = exchange_service.list_files(user_id=user.id)
            await safe_edit_message_text(
                callback.message,
                text=build_data_exchange_files_response(files),
                reply_markup=build_data_exchange_files_keyboard(files=files),
            )
            prepared_count = len(export_result.files)
            await callback.answer(
                "Экспорт подготовлен." if prepared_count == 1 else "Файлы экспорта подготовлены."
            )
            return

        exchange_file = DataExchangeFileRepository(session).get_by_id_for_user(
            file_id=callback_data.file_id,
            user_id=user.id,
        )
        if exchange_file is None:
            await callback.answer("Файл уже удалён или недоступен.", show_alert=True)
            return

        if callback_data.action == "delete":
            exchange_service.delete_file(exchange_file=exchange_file)
            files = exchange_service.list_files(user_id=user.id)
            await safe_edit_message_text(
                callback.message,
                text=build_data_exchange_files_response(files),
                reply_markup=build_data_exchange_files_keyboard(files=files),
            )
            await callback.answer("Файл удалён.")
            return

        if callback_data.action == "import":
            try:
                import_result = exchange_service.import_file(exchange_file=exchange_file, user=user)
            except DuplicateDataRowError as exc:
                DataExchangeFileRepository(session).mark_error(
                    file_id=exchange_file.id,
                    processing_message=str(exc),
                )
                files = exchange_service.list_files(user_id=user.id)
                await safe_edit_message_text(
                    callback.message,
                    text=build_data_exchange_files_response(files),
                    reply_markup=build_data_exchange_files_keyboard(files=files),
                )
                await callback.answer("Импорт не выполнен.", show_alert=True)
                await callback.message.answer(
                    "Импорт не завершён.\n"
                    f"Причина: {exc}"
                )
                return
            except ValueError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            files = exchange_service.list_files(user_id=user.id)
            await safe_edit_message_text(
                callback.message,
                text=build_data_exchange_files_response(files),
                reply_markup=build_data_exchange_files_keyboard(files=files),
            )
            await callback.answer("Импорт выполнен.")
            if import_result.contract_type == CSV_CONTRACT_TYPE_WORKOUT:
                completion_text = (
                    "Импорт завершён.\n"
                    f"- тренировок: {import_result.workout_entry_count}\n"
                    "Файл помечен как обработанный."
                )
            else:
                completion_text = (
                    "Импорт завершён.\n"
                    f"- записей еды: {import_result.food_entry_count}\n"
                    f"- записей воды: {import_result.water_entry_count}\n"
                    "Файл помечен как обработанный."
                )
            await callback.message.answer(completion_text)
            return

        if callback_data.action == "download":
            file_path = exchange_service.get_download_path(exchange_file=exchange_file)
            input_file = FSInputFile(file_path, filename=exchange_file.original_filename)
            await callback.message.answer_document(input_file)
            if exchange_file.status is not DataExchangeStatus.PROCESSED:
                exchange_service.mark_export_downloaded(exchange_file=exchange_file)
            files = exchange_service.list_files(user_id=user.id)
            await safe_edit_message_text(
                callback.message,
                text=build_data_exchange_files_response(files),
                reply_markup=build_data_exchange_files_keyboard(files=files),
            )
            await callback.answer("Файл отправлен.")
            return

        await callback.answer("Неизвестное действие.", show_alert=True)


@router.message(F.document)
async def handle_document_upload(
    message: Message,
    session_factory: sessionmaker[Session],
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    temp_path: Path | None = None
    try:
        temp_path, original_filename = await download_message_document_to_temp_file(message)
        with session_scope(session_factory) as session:
            _, _user_id = ensure_user_registered(message, session)
            user = UserRepository(session).get_by_telegram_user_id(message.from_user.id)
            if user is None:
                raise RuntimeError("User profile was not found after registration")
            exchange_service = DataExchangeService(session)
            try:
                sha256, validation_result = exchange_service.validate_import_file(
                    user=user,
                    source_path=temp_path,
                    original_filename=original_filename,
                )
                exchange_service.create_import_file(
                    user=user,
                    source_path=temp_path,
                    original_filename=original_filename,
                    sha256=sha256,
                    validation_result=validation_result,
                )
            except UnsupportedExchangeFileError as exc:
                await message.answer(str(exc), reply_markup=build_main_keyboard())
                return
            except DuplicateFileError as exc:
                await message.answer(
                    "Файл не принят.\n\n"
                    f"{exc}",
                    reply_markup=build_main_keyboard(),
                )
                return
            except DuplicateDataRowError as exc:
                await message.answer(
                    "Файл не принят.\n\n"
                    f"{exc}",
                    reply_markup=build_main_keyboard(),
                )
                return
            except FileLimitExceededError as exc:
                await message.answer(str(exc), reply_markup=build_main_keyboard())
                return

        response_text = build_import_validation_response_text(validation_result)

        await message.answer(
            response_text,
            reply_markup=build_main_keyboard(),
        )
    finally:
        if temp_path is not None:
            with suppress(FileNotFoundError):
                temp_path.unlink()
 
 
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
            workout_logging_enabled=user.workout_logging_enabled,
            summary_preference=preference,
            metric_deltas={"water": 250.0},
        )

    await message.answer(
        build_write_confirmation_response(saved_items, day_report=day_report),
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
    settings: Settings | None = None,
    admin_user_ids: tuple[int, ...] = (),
) -> None:
    if not await require_user_access(message, session_factory, admin_user_ids):
        return

    typing_task = await start_typing_indicator(message)
    try:
        active_conversation_session_id: int | None = None
        initial_user_id: int | None = None
        with session_scope(session_factory) as session:
            _, user_id = ensure_user_registered(message, session)
            initial_user_id = user_id
            reply_to_message = getattr(message, "reply_to_message", None)
            reply_to_message_id = getattr(reply_to_message, "message_id", None)
            chat = getattr(message, "chat", None)
            chat_id = getattr(chat, "id", None)
            if reply_to_message_id is not None and chat_id is not None:
                replied_session = ConversationMessageRepository(session).get_session_by_assistant_message(
                    telegram_chat_id=chat_id,
                    telegram_message_id=reply_to_message_id,
                )
                if replied_session is not None:
                    active_conversation_session_id = replied_session.id

            if active_conversation_session_id is None:
                active_conversation_session = ConversationSessionRepository(session).get_active_for_user(
                    user_id=user_id,
                    reference_at=datetime.now(timezone.utc),
                )
                if active_conversation_session is not None:
                    active_conversation_session_id = active_conversation_session.id

        extraction_request = await build_extraction_request(message)
        if extraction_request is None:
            await message.answer(
                "Сейчас я умею принимать текстовые записи, фото еды и кнопку воды. Если хочешь совет, задай вопрос текстом.",
                reply_markup=build_main_keyboard(),
            )
            return

        if is_photo_media_group_message(message):
            await message.answer(
                "Пока я умею разбирать только одно изображение за раз. Пришли одно основное фото или один скриншот.",
                reply_markup=build_main_keyboard(),
            )
            return

        routing_decision = message_routing_service.route(
            extraction_request,
            has_active_conversation_session=active_conversation_session_id is not None,
        )
        if routing_decision.route == CONVERSATION:
            current_time = datetime.now(timezone.utc)
            with session_scope(session_factory) as session:
                _, user_id = ensure_user_registered(message, session)
                runtime_bundle = None
                if settings is not None:
                    runtime_bundle = build_user_llm_runtime_bundle(
                        session=session,
                        settings=settings,
                        user_id=user_id,
                        telegram_user_id=message.from_user.id,
                        admin_user_ids=admin_user_ids,
                        fallback_extraction_service=extraction_service,
                        fallback_nutrition_service=nutrition_service,
                        fallback_conversation_service=conversation_service,
                    )
                    if (
                        runtime_bundle.conversation_access is not None
                        and not runtime_bundle.conversation_access.is_available
                    ):
                        await message.answer(
                            build_provider_unavailable_message(runtime_bundle.conversation_access),
                            reply_markup=build_main_keyboard(),
                            **build_reply_kwargs(message),
                        )
                        return
                user = UserRepository(session).get_by_telegram_user_id(message.from_user.id)
                if user is None:
                    raise RuntimeError("User profile was not found after registration")
                summary_preference, _created = UserSummaryPreferenceRepository(session).get_or_create(user_id=user_id)
                session_repository = ConversationSessionRepository(session)
                if active_conversation_session_id is not None:
                    conversation_session = session.get(ConversationSession, active_conversation_session_id)
                    if conversation_session is None:
                        conversation_session = session_repository.create_or_get_active(
                            user_id=user_id,
                            reference_at=current_time,
                        )
                else:
                    conversation_session = session_repository.create_or_get_active(
                        user_id=user_id,
                        reference_at=current_time,
                    )
                conversation_session_id = conversation_session.id
                recent_turns = ConversationMessageRepository(session).list_recent_for_session(
                    session_id=conversation_session_id,
                    limit=6,
                )
                factual_context = NutritionCoachContextBuilder(session).build(
                    user_id=user_id,
                    timezone_name=user.timezone,
                    nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
                    reference_at=current_time,
                    workout_logging_enabled=user.workout_logging_enabled,
                )
                conversation_reply = await asyncio.to_thread(
                    (runtime_bundle.conversation_service if runtime_bundle is not None else conversation_service).reply,
                    user_message=extraction_request.text or "",
                    factual_context=factual_context,
                    session_summary=conversation_session.summary_text,
                    recent_turns=[turn for turn in recent_turns],
                    images=extraction_request.images,
                )
            sent_message = await message.answer(
                build_conversation_response(conversation_reply.text),
                reply_markup=build_main_keyboard(),
                parse_mode=None,
                **build_reply_kwargs(message),
            )
            with session_scope(session_factory) as session:
                ConversationMessageRepository(session).create(
                    session_id=conversation_session_id,
                    role=ConversationMessageRole.USER,
                    content=extraction_request.text or "",
                    created_at=current_time,
                )
                ConversationMessageRepository(session).create(
                    session_id=conversation_session_id,
                    role=ConversationMessageRole.ASSISTANT,
                    content=conversation_reply.text,
                    created_at=current_time,
                    telegram_chat_id=getattr(getattr(sent_message, "chat", None), "id", None),
                    telegram_message_id=getattr(sent_message, "message_id", None),
                )
                ConversationSessionRepository(session).update_summary_and_touch(
                    session_id=conversation_session_id,
                    summary_text=conversation_reply.updated_session_summary,
                    last_message_at=current_time,
                )
            return

        if routing_decision.route == AMBIGUOUS:
            await message.answer(
                build_ambiguous_message_response(),
                reply_markup=build_main_keyboard(),
                **build_reply_kwargs(message),
            )
            return

        resolved_extraction_service = extraction_service
        if settings is not None and initial_user_id is not None:
            with session_scope(session_factory) as session:
                runtime_bundle = build_user_llm_runtime_bundle(
                    session=session,
                    settings=settings,
                    user_id=initial_user_id,
                    telegram_user_id=message.from_user.id,
                    admin_user_ids=admin_user_ids,
                    fallback_extraction_service=extraction_service,
                    fallback_nutrition_service=nutrition_service,
                    fallback_conversation_service=conversation_service,
                )
                if runtime_bundle.extraction_access is not None and not runtime_bundle.extraction_access.is_available:
                    await message.answer(
                        build_provider_unavailable_message(runtime_bundle.extraction_access),
                        reply_markup=build_main_keyboard(),
                    )
                    return
                resolved_extraction_service = runtime_bundle.extraction_service

        extraction_result = await asyncio.to_thread(resolved_extraction_service.extract, extraction_request)

        if isinstance(extraction_result, InvalidExtractionPayload):
            if extraction_result.is_llm:
                await asyncio.to_thread(
                    log_llm_issue,
                    session_factory=session_factory,
                    stage=LLMIssueStage.EXTRACTION,
                    error_code=extraction_result.error_code,
                    provider=extraction_result.provider,
                    model=extraction_result.model,
                    telegram_user_id=getattr(getattr(message, "from_user", None), "id", None),
                    username=getattr(getattr(message, "from_user", None), "username", None),
                    request_text=extraction_request.text,
                    raw_payload=extraction_result.raw_payload,
                    technical_message=extraction_result.technical_message,
                )
            await message.answer(
                extraction_result.message,
                reply_markup=build_main_keyboard(),
            )
            return

        if extraction_result is None:
            await message.answer(
                "Не смог уверенно распознать запись. Попробуй написать её короче, например `омлет и кофе`, или отправь другое фото.",
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
                runtime_bundle = None
                if settings is not None:
                    runtime_bundle = build_user_llm_runtime_bundle(
                        session=session,
                        settings=settings,
                        user_id=user_id,
                        telegram_user_id=message.from_user.id,
                        admin_user_ids=admin_user_ids,
                        fallback_extraction_service=extraction_service,
                        fallback_nutrition_service=nutrition_service,
                        fallback_conversation_service=conversation_service,
                    )
                if payload_contains_workout_entries(extraction_result.payload) and not user.workout_logging_enabled:
                    await message.answer(
                        "Запись тренировок сейчас выключена. Включи её в /settings, если хочешь сохранять такие сообщения.",
                        reply_markup=build_main_keyboard(),
                    )
                    return
                contains_food_entries = any(
                    extracted_entry.type is EntryType.FOOD
                    for extracted_entry in extraction_result.payload.entries
                )
                if (
                    contains_food_entries
                    and runtime_bundle is not None
                    and runtime_bundle.nutrition_access is not None
                    and not runtime_bundle.nutrition_access.is_available
                ):
                    raise FoodWriteFlowError(
                        build_provider_unavailable_message(runtime_bundle.nutrition_access)
                    )

                saved_food_entries: list = []
                saved_items = build_saved_items_from_payload(extraction_result.payload)
                extracted_workout_metric_lines = build_extracted_workout_metric_lines(extraction_result.payload)
                occurred_at_values: list[datetime] = []
                for extracted_entry in extraction_result.payload.entries:
                    occurred_at = extracted_entry.occurred_at or datetime.now(timezone.utc)
                    entry_extraction_raw_payload = json.dumps(
                        {"entries": [extracted_entry.model_dump(mode="json")]},
                        ensure_ascii=False,
                    )
                    saved_entry = EntryRepository(session).create(
                        user_id=user_id,
                        entry_type=extracted_entry.type,
                        occurred_at=occurred_at,
                        source_text=extraction_request.text if extracted_entry.type is EntryType.WORKOUT else None,
                        extraction_provider=extraction_result.extraction_provider,
                        extraction_model=extraction_result.extraction_model,
                        extraction_raw_payload=entry_extraction_raw_payload,
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
                        saved_food_entries.append(saved_entry)
                    if extracted_entry.type is EntryType.WORKOUT:
                        persisted_items = sorted(saved_entry.items, key=lambda current: current.position)
                        for persisted_item, extracted_item in zip(persisted_items, extracted_entry.items):
                            if not extracted_item.metrics:
                                continue
                            EntryItemMetricRepository(session).upsert_metrics(
                                entry_item_id=persisted_item.id,
                                metric_values=[
                                    EntryItemMetricValue(
                                        code=metric.code,
                                        value=metric.value,
                                        confidence=metric.confidence,
                                    )
                                    for metric in extracted_item.metrics
                                ],
                            )
                            workout_calories = next(
                                (
                                    metric
                                    for metric in extracted_item.metrics
                                    if metric.code == "workout_calories"
                                ),
                                None,
                            )
                            if workout_calories is not None:
                                EntryItemMetricRepository(session).upsert_metrics(
                                    entry_item_id=persisted_item.id,
                                    metric_values=[
                                        EntryItemMetricValue(
                                            code="workout_calorie_credit",
                                            value=calculate_default_workout_calorie_credit(workout_calories.value),
                                            confidence=workout_calories.confidence,
                                        )
                                    ],
                                )

                if saved_food_entries:
                    nutrition_flow_result = StoredEntryNutritionEstimationUseCase(
                        session,
                        runtime_bundle.nutrition_service if runtime_bundle is not None else nutrition_service,
                    ).run(entry_ids=[entry.id for entry in saved_food_entries])
                    if isinstance(nutrition_flow_result, FailedNutritionEstimation):
                        raise FoodWriteFlowError(
                            nutrition_flow_result.message,
                            issue=nutrition_flow_result.issue,
                        )
                    if isinstance(nutrition_flow_result, SkippedNutritionEstimation):
                        raise FoodWriteFlowError(nutrition_flow_result.reason)
                    nutrition_result = nutrition_flow_result

                summary_dates = resolve_summary_dates_for_occurred_at_values(
                    occurred_at_values=occurred_at_values,
                    timezone_name=user.timezone,
                    nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
                )
                if len(summary_dates) == 1 and (
                    payload_contains_food_or_water_entries(extraction_result.payload)
                    or payload_contains_credit_eligible_workout_entries(extraction_result.payload)
                ):
                    summary_date = next(iter(summary_dates))
                    metric_deltas = resolve_metric_deltas(
                        saved_items=saved_items,
                        nutrition_result=nutrition_result,
                    )
                    coach_comment: str | None = None
                    if saved_food_entries:
                        factual_context = NutritionCoachContextBuilder(session).build(
                            user_id=user_id,
                            timezone_name=user.timezone,
                            nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
                            reference_at=max(occurred_at_values),
                            workout_logging_enabled=user.workout_logging_enabled,
                        )
                        try:
                            coach_comment = await asyncio.to_thread(
                                (
                                    runtime_bundle.conversation_service
                                    if runtime_bundle is not None
                                    else conversation_service
                                ).comment_on_food_write,
                                saved_items=[
                                    format_saved_item_line(item.name, item.quantity, item.unit).removeprefix("- ")
                                    for item in saved_items
                                ],
                                factual_context=factual_context,
                                metric_deltas=metric_deltas,
                            )
                        except Exception:
                            logger.exception("Nutrition coach post-entry comment failed inside food write flow")
                            coach_comment = None
                        if coach_comment:
                            for saved_food_entry in saved_food_entries:
                                saved_food_entry.llm_comment = coach_comment
                    confirmation_text = build_write_confirmation_response(
                        saved_items,
                        extra_lines=extracted_workout_metric_lines,
                        day_report=build_daily_report_for_summary_date(
                            session=session,
                            user_id=user_id,
                            timezone_name=user.timezone,
                            summary_date=summary_date,
                            workout_logging_enabled=user.workout_logging_enabled,
                            summary_preference=summary_preference,
                            metric_deltas=metric_deltas,
                        ),
                        coach_comment=coach_comment,
                    )
                else:
                    confirmation_text = build_write_confirmation_response(
                        saved_items,
                        extra_lines=extracted_workout_metric_lines,
                    )
        except FoodWriteFlowError as exc:
            issue = getattr(exc, "issue", None)
            if issue is not None and issue.is_llm:
                await asyncio.to_thread(
                    log_llm_issue,
                    session_factory=session_factory,
                    stage=LLMIssueStage.NUTRITION,
                    error_code=issue.error_code,
                    provider=issue.provider,
                    model=issue.model,
                    telegram_user_id=getattr(getattr(message, "from_user", None), "id", None),
                    username=getattr(getattr(message, "from_user", None), "username", None),
                    request_text=extraction_request.text,
                    raw_payload=issue.raw_payload,
                    technical_message=issue.technical_message,
                )
            await message.answer(str(exc), reply_markup=build_main_keyboard())
            return

        await message.answer(
            confirmation_text,
            reply_markup=build_main_keyboard(),
        )
    finally:
        if typing_task is not None:
            typing_task.cancel()
            with suppress(asyncio.CancelledError):
                await typing_task
