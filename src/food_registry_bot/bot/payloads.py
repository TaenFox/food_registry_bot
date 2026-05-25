from food_registry_bot.extraction.contract import (
    ExtractedJournalEntry as NormalizedEntryPayload,
)
from food_registry_bot.extraction.contract import (
    ExtractedJournalItem as NormalizedEntryItemPayload,
)
from food_registry_bot.extraction.contract import (
    ExtractedJournalPayload as NormalizedJournalPayload,
)
from aiogram.filters.callback_data import CallbackData


class SummarySettingsCallback(CallbackData, prefix="summary_settings"):
    action: str


class RecentEntryDeleteCallback(CallbackData, prefix="recent_delete"):
    action: str
    entry_id: int = 0
    page: int = 0
    count: int = 5


class DataExchangeFileCallback(CallbackData, prefix="data_exchange"):
    action: str
    file_id: int = 0


class PeriodReportCallback(CallbackData, prefix="period_report"):
    action: str
    period_days: int = 8


class AdminDeleteEntriesCallback(CallbackData, prefix="admin_delete_entries"):
    action: str
    telegram_user_id: int = 0

__all__ = [
    "AdminDeleteEntriesCallback",
    "DataExchangeFileCallback",
    "NormalizedEntryItemPayload",
    "NormalizedEntryPayload",
    "NormalizedJournalPayload",
    "PeriodReportCallback",
    "RecentEntryDeleteCallback",
    "SummarySettingsCallback",
]
