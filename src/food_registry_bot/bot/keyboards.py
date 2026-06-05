from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from food_registry_bot.bot.payloads import (
    AdminPanelCallback,
    DataExchangeFileCallback,
    GoalMessageCallback,
    PeriodReportCallback,
    ProviderMenuCallback,
    RecentEntryActionCallback,
    RecentEntryDeleteCallback,
    SummarySettingsCallback,
)
from food_registry_bot.db.models import DataExchangeDirection, DataExchangeFile, DataExchangeStatus

TODAY_BUTTON_TEXT = "Сегодня"
RECENT_BUTTON_TEXT = "Недавние"
SETTINGS_BUTTON_TEXT = "Настройки"
REPORT_BUTTON_TEXT = "Отчёт"
WATER_250_ML_BUTTON_TEXT = "Вода 250 мл"
SUMMARY_DISPLAY_MODE_BUTTON_LABELS = {
    "text": "текст",
    "bars": "бары",
}
PERIOD_REPORT_PERIOD_SEQUENCE = (8, 16, 32)


def build_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=TODAY_BUTTON_TEXT), KeyboardButton(text=RECENT_BUTTON_TEXT)],
            [KeyboardButton(text=SETTINGS_BUTTON_TEXT), KeyboardButton(text=REPORT_BUTTON_TEXT)],
            [KeyboardButton(text=WATER_250_ML_BUTTON_TEXT)],
        ],
        is_persistent=True,
        resize_keyboard=True,
        input_field_placeholder="Напиши сообщение или выбери действие",
    )


def build_settings_root_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Цели", callback_data=SummarySettingsCallback(action="open_goals").pack())],
            [InlineKeyboardButton(text="Метрики", callback_data=SummarySettingsCallback(action="open_metrics").pack())],
            [InlineKeyboardButton(text="Отображение", callback_data=SummarySettingsCallback(action="open_display").pack())],
            [InlineKeyboardButton(text="Отчёты", callback_data=SummarySettingsCallback(action="open_reports").pack())],
            [InlineKeyboardButton(text="Диеты", callback_data=SummarySettingsCallback(action="open_diets").pack())],
            _build_close_row(SummarySettingsCallback(action="close").pack()),
        ]
    )


def build_settings_goals_keyboard(
    *,
    calorie_goal: int,
    protein_goal: int,
    fat_goal: int,
    carbs_goal: int,
    fiber_goal: int,
    water_goal: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="−100",
                    callback_data=SummarySettingsCallback(action="goal_dec_calories").pack(),
                ),
                InlineKeyboardButton(
                    text="−50",
                    callback_data=SummarySettingsCallback(action="goal_half_dec_calories").pack(),
                ),
                InlineKeyboardButton(
                    text="К",
                    callback_data=SummarySettingsCallback(action="noop").pack(),
                ),
                InlineKeyboardButton(
                    text="+50",
                    callback_data=SummarySettingsCallback(action="goal_half_inc_calories").pack(),
                ),
                InlineKeyboardButton(
                    text="+100",
                    callback_data=SummarySettingsCallback(action="goal_inc_calories").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="−10",
                    callback_data=SummarySettingsCallback(action="goal_dec_protein").pack(),
                ),
                InlineKeyboardButton(
                    text="−5",
                    callback_data=SummarySettingsCallback(action="goal_half_dec_protein").pack(),
                ),
                InlineKeyboardButton(
                    text="Б",
                    callback_data=SummarySettingsCallback(action="noop").pack(),
                ),
                InlineKeyboardButton(
                    text="+5",
                    callback_data=SummarySettingsCallback(action="goal_half_inc_protein").pack(),
                ),
                InlineKeyboardButton(
                    text="+10",
                    callback_data=SummarySettingsCallback(action="goal_inc_protein").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="−10",
                    callback_data=SummarySettingsCallback(action="goal_dec_fat").pack(),
                ),
                InlineKeyboardButton(
                    text="−5",
                    callback_data=SummarySettingsCallback(action="goal_half_dec_fat").pack(),
                ),
                InlineKeyboardButton(
                    text="Ж",
                    callback_data=SummarySettingsCallback(action="noop").pack(),
                ),
                InlineKeyboardButton(
                    text="+5",
                    callback_data=SummarySettingsCallback(action="goal_half_inc_fat").pack(),
                ),
                InlineKeyboardButton(
                    text="+10",
                    callback_data=SummarySettingsCallback(action="goal_inc_fat").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="−10",
                    callback_data=SummarySettingsCallback(action="goal_dec_carbs").pack(),
                ),
                InlineKeyboardButton(
                    text="−5",
                    callback_data=SummarySettingsCallback(action="goal_half_dec_carbs").pack(),
                ),
                InlineKeyboardButton(
                    text="У",
                    callback_data=SummarySettingsCallback(action="noop").pack(),
                ),
                InlineKeyboardButton(
                    text="+5",
                    callback_data=SummarySettingsCallback(action="goal_half_inc_carbs").pack(),
                ),
                InlineKeyboardButton(
                    text="+10",
                    callback_data=SummarySettingsCallback(action="goal_inc_carbs").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="−10",
                    callback_data=SummarySettingsCallback(action="goal_dec_fiber").pack(),
                ),
                InlineKeyboardButton(
                    text="−5",
                    callback_data=SummarySettingsCallback(action="goal_half_dec_fiber").pack(),
                ),
                InlineKeyboardButton(
                    text="Кл",
                    callback_data=SummarySettingsCallback(action="noop").pack(),
                ),
                InlineKeyboardButton(
                    text="+5",
                    callback_data=SummarySettingsCallback(action="goal_half_inc_fiber").pack(),
                ),
                InlineKeyboardButton(
                    text="+10",
                    callback_data=SummarySettingsCallback(action="goal_inc_fiber").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="−100",
                    callback_data=SummarySettingsCallback(action="goal_dec_water").pack(),
                ),
                InlineKeyboardButton(
                    text="−50",
                    callback_data=SummarySettingsCallback(action="goal_half_dec_water").pack(),
                ),
                InlineKeyboardButton(
                    text="В",
                    callback_data=SummarySettingsCallback(action="noop").pack(),
                ),
                InlineKeyboardButton(
                    text="+50",
                    callback_data=SummarySettingsCallback(action="goal_half_inc_water").pack(),
                ),
                InlineKeyboardButton(
                    text="+100",
                    callback_data=SummarySettingsCallback(action="goal_inc_water").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="К разделам",
                    callback_data=SummarySettingsCallback(action="back_root").pack(),
                )
            ],
            _build_close_row(SummarySettingsCallback(action="close").pack()),
        ]
    )


def build_settings_metrics_keyboard(
    *,
    show_calories: bool,
    show_protein: bool,
    show_fat: bool,
    show_carbs: bool,
    show_fiber: bool,
    show_water: bool,
    workout_logging_enabled: bool,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_build_settings_toggle_button("Калории", show_calories, "toggle_calories")],
            [_build_settings_toggle_button("Белки", show_protein, "toggle_protein")],
            [_build_settings_toggle_button("Жиры", show_fat, "toggle_fat")],
            [_build_settings_toggle_button("Углеводы", show_carbs, "toggle_carbs")],
            [_build_settings_toggle_button("Клетчатка", show_fiber, "toggle_fiber")],
            [_build_settings_toggle_button("Вода", show_water, "toggle_water")],
            [_build_settings_toggle_button("Тренировки", workout_logging_enabled, "toggle_workout_logging")],
            [InlineKeyboardButton(text="К разделам", callback_data=SummarySettingsCallback(action="back_root").pack())],
            _build_close_row(SummarySettingsCallback(action="close").pack()),
        ]
    )


def build_settings_display_keyboard(
    *,
    show_day_progress_bar: bool,
    summary_display_mode: str,
    show_post_entry_delta_suffix: bool,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_build_settings_toggle_button("Прогресс дня", show_day_progress_bar, "toggle_day_progress_bar")],
            [
                InlineKeyboardButton(
                    text=f"Текст/бары: {SUMMARY_DISPLAY_MODE_BUTTON_LABELS[summary_display_mode]}",
                    callback_data=SummarySettingsCallback(action="cycle_summary_display_mode").pack(),
                )
            ],
            [_build_settings_toggle_button("Дельта записи", show_post_entry_delta_suffix, "toggle_post_entry_delta_suffix")],
            [InlineKeyboardButton(text="К разделам", callback_data=SummarySettingsCallback(action="back_root").pack())],
            _build_close_row(SummarySettingsCallback(action="close").pack()),
        ]
    )


def build_settings_reports_keyboard(
    *,
    nutrition_day_start_hour: int,
    report_goal_tolerance_percent: int,
    report_noticeable_entry_percentile: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Начало дня: {nutrition_day_start_hour:02d}:00",
                    callback_data=SummarySettingsCallback(action="cycle_nutrition_day_start_hour").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Допуск к цели: {report_goal_tolerance_percent}%",
                    callback_data=SummarySettingsCallback(action="cycle_report_goal_tolerance_percent").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Порог заметных записей: {report_noticeable_entry_percentile}%",
                    callback_data=SummarySettingsCallback(action="cycle_report_noticeable_entry_percentile").pack(),
                )
            ],
            [InlineKeyboardButton(text="К разделам", callback_data=SummarySettingsCallback(action="back_root").pack())],
            _build_close_row(SummarySettingsCallback(action="close").pack()),
        ]
    )


def build_settings_diets_keyboard(
    *,
    diet_buttons: list[tuple[str, str, bool]],
) -> InlineKeyboardMarkup:
    rows = [
        [_build_settings_toggle_button(f"Диета: {diet_name}", is_enabled, f"toggle_diet_{diet_code}")]
        for diet_code, diet_name, is_enabled in diet_buttons
    ]
    rows.append([InlineKeyboardButton(text="К разделам", callback_data=SummarySettingsCallback(action="back_root").pack())])
    rows.append(_build_close_row(SummarySettingsCallback(action="close").pack()))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_recent_entries_delete_keyboard(
    *,
    page: int,
    count: int,
    has_previous_page: bool,
    has_next_page: bool,
    has_entries: bool,
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
    if has_entries:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Открыть запись",
                    callback_data=RecentEntryActionCallback(action="open_entries", page=page, count=count).pack(),
                ),
                InlineKeyboardButton(
                    text="Повторить блюдо",
                    callback_data=RecentEntryActionCallback(action="open_unique_items", page=page, count=count).pack(),
                ),
            ]
        )
    rows.append(_build_close_row(RecentEntryDeleteCallback(action="close", page=page, count=count).pack()))
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
    rows.append(_build_close_row(RecentEntryDeleteCallback(action="close", page=page, count=count).pack()))
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
            ],
            _build_close_row(
                RecentEntryDeleteCallback(action="close", entry_id=entry_id, page=page, count=count).pack()
            ),
        ]
    )


def build_recent_entry_action_selection_keyboard(
    *,
    entry_buttons: list[tuple[str, str]],
    navigation_row: list[InlineKeyboardButton] | None,
    back_callback_data: str,
    close_callback_data: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=button_text,
                callback_data=callback_data,
            )
        ]
        for button_text, callback_data in entry_buttons
    ]
    if navigation_row:
        rows.append(navigation_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=back_callback_data,
            )
        ]
    )
    rows.append(_build_close_row(close_callback_data))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_recent_food_entry_keyboard(
    *,
    item_buttons: list[tuple[str, str]],
    repeat_entry_callback_data: str,
    delete_entry_callback_data: str,
    close_callback_data: str,
    back_callback_data: str | None = None,
    include_back_button: bool = True,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=button_text,
                callback_data=callback_data,
            )
        ]
        for button_text, callback_data in item_buttons
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="Повторить запись целиком",
                callback_data=repeat_entry_callback_data,
            ),
            InlineKeyboardButton(
                text="Удалить запись",
                callback_data=delete_entry_callback_data,
            ),
        ]
    )
    if include_back_button and back_callback_data is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Назад к записям",
                    callback_data=back_callback_data,
                )
            ]
        )
    rows.append(_build_close_row(close_callback_data))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_recent_non_food_entry_keyboard(
    *,
    delete_entry_callback_data: str,
    close_callback_data: str,
    back_callback_data: str | None = None,
    include_back_button: bool = True,
) -> InlineKeyboardMarkup:
    rows = [
            [
                InlineKeyboardButton(
                    text="Удалить запись",
                    callback_data=delete_entry_callback_data,
                )
            ],
    ]
    if include_back_button and back_callback_data is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Назад к записям",
                    callback_data=back_callback_data,
                )
            ]
        )
    rows.append(_build_close_row(close_callback_data))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_recent_food_item_keyboard(
    *,
    unit: str | None,
    can_adjust_portion: bool,
    adjustable_metric_codes: tuple[str, ...],
    delete_item_callback_data: str,
    repeat_item_callback_data: str,
    decrease_portion_callback_data: str | None,
    increase_portion_callback_data: str | None,
    metric_adjustment_buttons: list[tuple[str, str, str, str]],
    close_callback_data: str,
) -> InlineKeyboardMarkup:
    rows = [
            [
                InlineKeyboardButton(
                    text="Удалить блюдо",
                    callback_data=delete_item_callback_data,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Повторить сейчас",
                    callback_data=repeat_item_callback_data,
                )
            ],
    ]
    normalized_unit = unit.strip().lower() if unit is not None else None
    if can_adjust_portion and normalized_unit in {"g", "ml"} and decrease_portion_callback_data and increase_portion_callback_data:
        decrement_label = "-10 г" if normalized_unit == "g" else "-10 мл"
        increment_label = "+10 г" if normalized_unit == "g" else "+10 мл"
        rows.append(
            [
                InlineKeyboardButton(
                    text=decrement_label,
                    callback_data=decrease_portion_callback_data,
                ),
                InlineKeyboardButton(
                    text=increment_label,
                    callback_data=increase_portion_callback_data,
                ),
            ]
        )
    _ = adjustable_metric_codes
    for decrement_label, decrement_callback_data, increment_label, increment_callback_data in metric_adjustment_buttons:
        rows.append(
            [
                InlineKeyboardButton(
                    text=decrement_label,
                    callback_data=decrement_callback_data,
                ),
                InlineKeyboardButton(
                    text=increment_label,
                    callback_data=increment_callback_data,
                ),
            ]
        )
    rows.append(_build_close_row(close_callback_data))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_recent_food_item_delete_confirmation_keyboard(
    *,
    confirm_callback_data: str,
    cancel_callback_data: str,
    close_callback_data: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=confirm_callback_data,
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=cancel_callback_data,
                ),
            ],
            _build_close_row(close_callback_data),
        ]
    )


def build_recent_food_entry_delete_confirmation_keyboard(
    *,
    confirm_callback_data: str,
    cancel_callback_data: str,
    close_callback_data: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=confirm_callback_data,
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=cancel_callback_data,
                ),
            ],
            _build_close_row(close_callback_data),
        ]
    )


def build_post_entry_details_keyboard(*, details_callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подробнее",
                    callback_data=details_callback_data,
                )
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


def build_recent_entry_action_navigation_row(
    *,
    previous_callback_data: str | None,
    next_callback_data: str | None,
) -> list[InlineKeyboardButton]:
    row: list[InlineKeyboardButton] = []
    if previous_callback_data is not None:
        row.append(
            InlineKeyboardButton(
                text="← Назад",
                callback_data=previous_callback_data,
            )
        )
    if next_callback_data is not None:
        row.append(
            InlineKeyboardButton(
                text="Вперёд →",
                callback_data=next_callback_data,
            )
        )
    return row


def _build_admin_navigation_row(
    *,
    page: int,
    has_previous_page: bool,
    has_next_page: bool,
) -> list[InlineKeyboardButton]:
    row: list[InlineKeyboardButton] = []
    if has_previous_page:
        row.append(
            InlineKeyboardButton(
                text="← Назад",
                callback_data=AdminPanelCallback(action="open_users", page=page - 1).pack(),
            )
        )
    if has_next_page:
        row.append(
            InlineKeyboardButton(
                text="Вперёд →",
                callback_data=AdminPanelCallback(action="open_users", page=page + 1).pack(),
            )
        )
    return row


def build_admin_overview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Управление пользователями",
                    callback_data=AdminPanelCallback(action="open_users").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="LLM-ошибки",
                    callback_data=AdminPanelCallback(action="open_llm_issues", page=0).pack(),
                ),
            ],
            _build_close_row(AdminPanelCallback(action="close").pack()),
        ]
    )


def build_goal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            _build_close_row(GoalMessageCallback(action="close").pack()),
        ]
    )


def build_provider_connections_keyboard(
    *,
    can_use_project: bool,
    selection_mode: str,
    connection_buttons: list[tuple[str, str, str]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_use_project:
        project_prefix = "✓ " if selection_mode == "project" else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{project_prefix}Проектный",
                    callback_data=ProviderMenuCallback(action="use_project").pack(),
                )
            ]
        )

    for button_text, provider, model in connection_buttons:
        rows.append(
            [
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=ProviderMenuCallback(
                        action="open_connection",
                        provider=provider,
                        model=model,
                    ).pack(),
                )
            ]
        )

    rows.append(_build_close_row(ProviderMenuCallback(action="close").pack()))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_provider_connection_actions_keyboard(
    *,
    provider: str,
    model: str,
    can_choose: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_choose:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Выбрать",
                    callback_data=ProviderMenuCallback(
                        action="select_connection",
                        provider=provider,
                        model=model,
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Удалить",
                callback_data=ProviderMenuCallback(
                    action="delete_connection",
                    provider=provider,
                    model=model,
                ).pack(),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=ProviderMenuCallback(action="back").pack(),
            )
        ]
    )
    rows.append(_build_close_row(ProviderMenuCallback(action="close").pack()))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_user_list_keyboard(
    *,
    user_buttons: list[tuple[str, int]],
    page: int,
    has_previous_page: bool,
    has_next_page: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=button_text,
                callback_data=AdminPanelCallback(
                    action="open_user",
                    telegram_user_id=telegram_user_id,
                    page=page,
                ).pack(),
            )
        ]
        for button_text, telegram_user_id in user_buttons
    ]
    navigation_row = _build_admin_navigation_row(page=page, has_previous_page=has_previous_page, has_next_page=has_next_page)
    if navigation_row:
        rows.append(navigation_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="К панели",
                callback_data=AdminPanelCallback(action="overview").pack(),
            )
        ]
    )
    rows.append(_build_close_row(AdminPanelCallback(action="close").pack()))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_user_actions_keyboard(
    *,
    telegram_user_id: int,
    page: int,
    is_allowed: bool,
    is_admin: bool,
    account_category: str,
    temporary_internal_active: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if not is_admin:
        access_button_text = "Запретить доступ" if is_allowed else "Разрешить доступ"
        access_action = "deny_user" if is_allowed else "allow_user"
        rows.append(
            [
                InlineKeyboardButton(
                    text=access_button_text,
                    callback_data=AdminPanelCallback(
                        action=access_action,
                        telegram_user_id=telegram_user_id,
                        page=page,
                    ).pack(),
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Internal{' · текущая' if account_category == 'internal' else ''}",
                    callback_data=AdminPanelCallback(
                        action="set_internal_category",
                        telegram_user_id=telegram_user_id,
                        page=page,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=f"External{' · текущая' if account_category == 'external' else ''}",
                    callback_data=AdminPanelCallback(
                        action="set_external_category",
                        telegram_user_id=telegram_user_id,
                        page=page,
                    ).pack(),
                ),
            ]
        )
        if account_category != "internal":
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Продлить Internal на 24ч" if temporary_internal_active else "Internal на 24ч",
                        callback_data=AdminPanelCallback(
                            action="grant_temporary_internal",
                            telegram_user_id=telegram_user_id,
                            page=page,
                        ).pack(),
                    )
                ]
            )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Удалить данные пользователя",
                    callback_data=AdminPanelCallback(
                        action="prompt_delete_user_entries",
                        telegram_user_id=telegram_user_id,
                        page=page,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="К списку пользователей",
                    callback_data=AdminPanelCallback(action="open_users", page=page).pack(),
                )
            ],
            _build_close_row(AdminPanelCallback(action="close").pack()),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_llm_issues_keyboard(
    *,
    page: int,
    has_previous_page: bool,
    has_next_page: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation_row: list[InlineKeyboardButton] = []
    if has_previous_page:
        navigation_row.append(
            InlineKeyboardButton(
                text="← Назад",
                callback_data=AdminPanelCallback(action="open_llm_issues", page=page - 1).pack(),
            )
        )
    if has_next_page:
        navigation_row.append(
            InlineKeyboardButton(
                text="Вперёд →",
                callback_data=AdminPanelCallback(action="open_llm_issues", page=page + 1).pack(),
            )
        )
    if navigation_row:
        rows.append(navigation_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="К панели",
                callback_data=AdminPanelCallback(action="overview").pack(),
            )
        ]
    )
    rows.append(_build_close_row(AdminPanelCallback(action="close").pack()))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_delete_entries_confirmation_keyboard(*, telegram_user_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить удаление",
                    callback_data=AdminPanelCallback(
                        action="confirm_delete_user_entries",
                        telegram_user_id=telegram_user_id,
                        page=page,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=AdminPanelCallback(
                        action="open_user",
                        telegram_user_id=telegram_user_id,
                        page=page,
                    ).pack(),
                ),
            ],
            _build_close_row(AdminPanelCallback(action="close").pack()),
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
    rows.append(_build_close_row(DataExchangeFileCallback(action="close").pack()))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_period_report_keyboard(*, period_days: int) -> InlineKeyboardMarkup:
    next_period_days = _resolve_next_period_days(period_days)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Следующий период: {_format_period_days(next_period_days)}",
                    callback_data=PeriodReportCallback(action="cycle_period", period_days=period_days).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Динамика",
                    callback_data=PeriodReportCallback(action="open_dynamics", period_days=period_days).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Заметные записи пищи",
                    callback_data=PeriodReportCallback(action="open_noticeable", period_days=period_days).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Закрыть",
                    callback_data=PeriodReportCallback(action="close", period_days=period_days).pack(),
                )
            ],
        ]
    )


def _resolve_next_period_days(period_days: int) -> int:
    try:
        current_index = PERIOD_REPORT_PERIOD_SEQUENCE.index(period_days)
    except ValueError:
        return PERIOD_REPORT_PERIOD_SEQUENCE[0]
    return PERIOD_REPORT_PERIOD_SEQUENCE[(current_index + 1) % len(PERIOD_REPORT_PERIOD_SEQUENCE)]


def _format_period_days(period_days: int) -> str:
    if period_days == 32:
        return "32 дня"
    return f"{period_days} дней"


def build_period_report_dynamics_keyboard(*, period_days: int, metric_code: str, next_metric_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Следующая метрика",
                    callback_data=PeriodReportCallback(
                        action="cycle_dynamics_metric",
                        period_days=period_days,
                        metric_code=next_metric_code,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Закрыть",
                    callback_data=PeriodReportCallback(
                        action="close",
                        period_days=period_days,
                        metric_code=metric_code,
                    ).pack(),
                )
            ],
        ]
    )


def build_period_report_noticeable_keyboard(*, period_days: int, metric_code: str, next_metric_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Следующая метрика",
                    callback_data=PeriodReportCallback(
                        action="cycle_noticeable_metric",
                        period_days=period_days,
                        metric_code=next_metric_code,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Закрыть",
                    callback_data=PeriodReportCallback(
                        action="close",
                        period_days=period_days,
                        metric_code=metric_code,
                    ).pack(),
                )
            ],
        ]
    )


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


def _build_settings_toggle_button(label: str, is_enabled: bool, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=f"{label}: {'on' if is_enabled else 'off'}",
        callback_data=SummarySettingsCallback(action=action).pack(),
    )


def _build_close_row(callback_data: str) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text="Закрыть",
            callback_data=callback_data,
        )
    ]
