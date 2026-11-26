from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from food_registry_bot.db.models import Entry, EntryItem, EntryType
from food_registry_bot.db.repositories import (
    EntryItemCreate,
    EntryItemMetricRepository,
    EntryItemMetricValue,
    EntryRepository,
)
from food_registry_bot.nutrition.contract import SUPPORTED_NUTRITION_METRIC_CODES


def copy_food_items(
    session: Session,
    *,
    user_id: int,
    occurred_at: datetime,
    portions: list[tuple[EntryItem, int | None]],
    entry_type: EntryType = EntryType.FOOD,
) -> Entry:
    entry = EntryRepository(session).create(
        user_id=user_id,
        entry_type=entry_type,
        occurred_at=occurred_at,
        items=[
            EntryItemCreate(
                name=item.name,
                quantity=item.quantity if quantity is None else quantity,
                unit=item.unit,
                confidence=item.confidence,
                source_type=item.source_type,
            )
            for item, quantity in portions
        ],
    )
    metrics = EntryItemMetricRepository(session)
    for target, (source, quantity) in zip(sorted(entry.items, key=lambda item: item.position), portions):
        if (source.source_facts or {}).get("beverage") is True:
            target.source_facts = {"beverage": True}
        ratio = quantity / source.quantity if quantity is not None and source.quantity else 1
        values = []
        for metric in source.metrics:
            if metric.metric is None:
                continue
            code = metric.metric.code
            value = metric.value
            if ratio != 1 and code in SUPPORTED_NUTRITION_METRIC_CODES:
                value = round(value * ratio, 4)
            values.append(EntryItemMetricValue(code=code, value=value, confidence=metric.confidence))
        if values:
            metrics.upsert_metrics(entry_item_id=target.id, metric_values=values)
    return entry
