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

__all__ = [
    "NormalizedEntryItemPayload",
    "NormalizedEntryPayload",
    "NormalizedJournalPayload",
    "SummarySettingsCallback",
]
