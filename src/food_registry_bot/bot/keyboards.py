from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from food_registry_bot.bot.payloads import (
    AdminDeleteEntriesCallback,
    DataExchangeFileCallback,
    RecentEntryDeleteCallback,
    SummarySettingsCallback,
)
from food_registry_bot.db.models import DataExchangeDirection, DataExchangeFile, DataExchangeStatus

WATER_250_ML_BUTTON_TEXT = "Вода 250 мл"
SUMMARY_DISPLAY_MODE_BUTTON_LABELS = {
    "text": "текст",
    "bars": "бары",
}


def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=WATER_250_ML_BUTTON_TEXT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напиши сообщение или выбери действие",
    )


def build_summary_settings_keyboard(
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
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Тренировки: {'on' if workout_logging_enabled else 'off'}",
                    callback_data=SummarySettingsCallback(action="toggle_workout_logging").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Калории: {'on' if show_calories else 'off'}",
                    callback_data=SummarySettingsCallback(action="toggle_calories").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Белки: {'on' if show_protein else 'off'}",
                    callback_data=SummarySettingsCallback(action="toggle_protein").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Жиры: {'on' if show_fat else 'off'}",
                    callback_data=SummarySettingsCallback(action="toggle_fat").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Углеводы: {'on' if show_carbs else 'off'}",
                    callback_data=SummarySettingsCallback(action="toggle_carbs").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Клетчатка: {'on' if show_fiber else 'off'}",
                    callback_data=SummarySettingsCallback(action="toggle_fiber").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Вода: {'on' if show_water else 'off'}",
                    callback_data=SummarySettingsCallback(action="toggle_water").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Дельта записи: {'on' if show_post_entry_delta_suffix else 'off'}",
                    callback_data=SummarySettingsCallback(action="toggle_post_entry_delta_suffix").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Отображение: {SUMMARY_DISPLAY_MODE_BUTTON_LABELS[summary_display_mode]}",
                    callback_data=SummarySettingsCallback(action="cycle_summary_display_mode").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Начало дня: {nutrition_day_start_hour:02d}:00",
                    callback_data=SummarySettingsCallback(action="cycle_nutrition_day_start_hour").pack(),
                )
            ]
        ]
    )


def build_recent_entries_delete_keyboard(
    *,
    page: int,
    count: int,
    has_previous_page: bool,
    has_next_page: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation_row = _build_recent_entries_navigation_row(
        page=page,
        count=count,
        action="list",
        has_previous_page=has_previous_page,
        has_next_page=has_next_page,
    )
    if navigation_row:
        rows.append(navigation_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="Выбрать для удаления",
                callback_data=RecentEntryDeleteCallback(action="open", page=page, count=count).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_recent_entry_selection_keyboard(
    *,
    entry_buttons: list[tuple[str, int]],
    page: int,
    count: int,
    has_previous_page: bool,
    has_next_page: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=button_text,
                callback_data=RecentEntryDeleteCallback(
                    action="select",
                    entry_id=entry_id,
                    page=page,
                    count=count,
                ).pack(),
            )
        ]
        for button_text, entry_id in entry_buttons
    ]
    navigation_row = _build_recent_entries_navigation_row(
        page=page,
        count=count,
        action="open",
        has_previous_page=has_previous_page,
        has_next_page=has_next_page,
    )
    if navigation_row:
        rows.append(navigation_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="Отмена",
                callback_data=RecentEntryDeleteCallback(action="list", page=page, count=count).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_recent_entry_confirmation_keyboard(*, entry_id: int, page: int, count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=RecentEntryDeleteCallback(
                        action="confirm",
                        entry_id=entry_id,
                        page=page,
                        count=count,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=RecentEntryDeleteCallback(action="open", page=page, count=count).pack(),
                ),
            ]
        ]
    )


def _build_recent_entries_navigation_row(
    *,
    page: int,
    count: int,
    action: str,
    has_previous_page: bool,
    has_next_page: bool,
) -> list[InlineKeyboardButton]:
    row: list[InlineKeyboardButton] = []
    if has_previous_page:
        row.append(
            InlineKeyboardButton(
                text="← Назад",
                callback_data=RecentEntryDeleteCallback(action=action, page=page - 1, count=count).pack(),
            )
        )
    if has_next_page:
        row.append(
            InlineKeyboardButton(
                text="Вперёд →",
                callback_data=RecentEntryDeleteCallback(action=action, page=page + 1, count=count).pack(),
            )
        )
    return row


def build_admin_delete_entries_confirmation_keyboard(*, telegram_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить удаление",
                    callback_data=AdminDeleteEntriesCallback(
                        action="confirm",
                        telegram_user_id=telegram_user_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=AdminDeleteEntriesCallback(
                        action="cancel",
                        telegram_user_id=telegram_user_id,
                    ).pack(),
                ),
            ]
        ]
    )


def build_data_exchange_files_keyboard(*, files: list[DataExchangeFile]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="Создать экспорт",
                callback_data=DataExchangeFileCallback(action="create_export").pack(),
            )
        ]
    ]

    for exchange_file in files:
        primary_button = _build_data_exchange_primary_button(exchange_file)
        row = [primary_button] if primary_button is not None else []
        row.append(
            InlineKeyboardButton(
                text=f"Удалить #{exchange_file.id}",
                callback_data=DataExchangeFileCallback(action="delete", file_id=exchange_file.id).pack(),
            )
        )
        rows.append(row)

    rows.append(
        [
            InlineKeyboardButton(
                text="Обновить",
                callback_data=DataExchangeFileCallback(action="refresh").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_data_exchange_primary_button(exchange_file: DataExchangeFile) -> InlineKeyboardButton | None:
    if exchange_file.direction is DataExchangeDirection.IMPORT and exchange_file.status is not DataExchangeStatus.PROCESSED:
        return InlineKeyboardButton(
            text=f"Импортировать #{exchange_file.id}",
            callback_data=DataExchangeFileCallback(action="import", file_id=exchange_file.id).pack(),
        )
    if exchange_file.direction is DataExchangeDirection.EXPORT:
        return InlineKeyboardButton(
            text=f"Скачать #{exchange_file.id}",
            callback_data=DataExchangeFileCallback(action="download", file_id=exchange_file.id).pack(),
        )
    return None
