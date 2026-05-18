from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

WATER_250_ML_BUTTON_TEXT = "Вода 250 мл"


def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=WATER_250_ML_BUTTON_TEXT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напиши сообщение или выбери действие",
    )
