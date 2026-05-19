from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from food_registry_bot.bot.payloads import SummarySettingsCallback

WATER_250_ML_BUTTON_TEXT = "Вода 250 мл"


def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=WATER_250_ML_BUTTON_TEXT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напиши сообщение или выбери действие",
    )


def build_summary_settings_keyboard(*, show_calories: bool) -> InlineKeyboardMarkup:
    status = "on" if show_calories else "off"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Калории: {status}",
                    callback_data=SummarySettingsCallback(action="toggle_show_calories").pack(),
                )
            ]
        ]
    )
