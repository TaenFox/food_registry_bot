from __future__ import annotations

from food_registry_bot.db.models import Entry, EntryItem, EntryType
from food_registry_bot.nutrition.contract import SUPPORTED_NUTRITION_METRIC_CODES, NutritionEstimationItemInput


def is_plain_water(item: EntryItem) -> bool:
    return item.name == "water"


def is_beverage(item: EntryItem) -> bool:
    return is_plain_water(item) or (item.source_facts or {}).get("beverage") is True


def beverage_volume(item: EntryItem) -> int | None:
    if (is_beverage(item) and NutritionEstimationItemInput.normalize_unit(item.unit) == "ml"
            and item.quantity is not None and item.quantity > 0):
        return item.quantity
    return None


def nutrition_items(entry: Entry) -> list[EntryItem]:
    return [
        item for item in entry.items
        if (entry.entry_type is EntryType.FOOD and not is_plain_water(item))
        or (is_plain_water(item) and any(
            metric.metric is not None and metric.metric.code in SUPPORTED_NUTRITION_METRIC_CODES
            for metric in item.metrics
        ))
    ]
