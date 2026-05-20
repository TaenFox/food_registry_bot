from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from food_registry_bot.bot.payloads import RecentEntryDeleteCallback, SummarySettingsCallback

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


def build_recent_entries_delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Выбрать для удаления",
                    callback_data=RecentEntryDeleteCallback(action="open").pack(),
                )
            ]
        ]
    )


def build_recent_entry_selection_keyboard(
    *,
    entry_buttons: list[tuple[str, int]],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=button_text,
                callback_data=RecentEntryDeleteCallback(action="select", entry_id=entry_id).pack(),
            )
        ]
        for button_text, entry_id in entry_buttons
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="Отмена",
                callback_data=RecentEntryDeleteCallback(action="close").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_recent_entry_confirmation_keyboard(*, entry_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=RecentEntryDeleteCallback(action="confirm", entry_id=entry_id).pack(),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=RecentEntryDeleteCallback(action="open").pack(),
                ),
            ]
        ]
    )
