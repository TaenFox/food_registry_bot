from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from food_registry_bot.db.models import Entry, EntryItem, EntryItemMetric, EntryType, User
from food_registry_bot.db.repositories import (
    EntryItemMetricRepository,
    EntryItemMetricValue,
    UserSummaryPreferenceRepository,
)
from food_registry_bot.nutrition.contract import SUPPORTED_NUTRITION_METRIC_CODES
from food_registry_bot.nutrition.daily_summary import resolve_local_summary_date
from food_registry_bot.nutrition.journal_adapter import preserve_entry_item_source_facts

DIARY_METRICS = ("calories", "protein", "fat", "carbs")
PositiveInteger = Annotated[int, Field(gt=0, le=2_147_483_647)]
MetricValue = Annotated[FiniteFloat, Field(ge=0)]


class DiaryAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    entry_id: PositiveInteger
    item_id: PositiveInteger


class MoveDish(DiaryAction):
    action: Literal["move"]
    target_entry_id: PositiveInteger
    confirm_delete: bool = False


class Per100gValues(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    calories: Optional[MetricValue] = None
    protein: Optional[MetricValue] = None
    fat: Optional[MetricValue] = None
    carbs: Optional[MetricValue] = None


class EditDish(DiaryAction):
    action: Literal["edit"]
    revision: str = Field(pattern=r"^[a-f0-9]{32}$")
    weight: PositiveInteger
    per_100g: Per100gValues = Field(default_factory=Per100gValues)


DiaryMutation = Union[MoveDish, EditDish]
DIARY_MUTATION = TypeAdapter(Annotated[DiaryMutation, Field(discriminator="action")])


class DiaryEditError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def dish_metric_values(item: EntryItem) -> dict[str, float]:
    return {
        metric.metric.code: metric.value
        for metric in item.metrics
        if metric.metric is not None and metric.metric.code in SUPPORTED_NUTRITION_METRIC_CODES
    }


def dish_revision(item: EntryItem, metrics: dict[str, float]) -> str:
    state = json.dumps([item.quantity, item.unit, metrics], sort_keys=True)
    return hashlib.sha256(state.encode()).hexdigest()[:32]


def _lock_entries(
    session: Session, user_id: int, entry_ids: set[int], *, lock_metrics: bool
) -> dict[int, Entry]:
    entries = session.scalars(
        select(Entry)
        .where(
            Entry.id.in_(entry_ids), Entry.user_id == user_id, Entry.entry_type == EntryType.FOOD
        )
        .order_by(Entry.id)
        .with_for_update()
    ).all()
    if len(entries) != len(entry_ids):
        raise DiaryEditError(404, "entry_missing")

    item_ids = session.scalars(
        select(EntryItem.id)
        .where(EntryItem.entry_id.in_(entry_ids))
        .order_by(EntryItem.id)
        .with_for_update()
    ).all()
    if lock_metrics:
        session.scalars(
            select(EntryItemMetric.id)
            .where(EntryItemMetric.entry_item_id.in_(item_ids))
            .order_by(EntryItemMetric.id)
            .with_for_update()
        ).all()
    return {entry.id: entry for entry in entries}


def apply_diary_mutation(session: Session, user: User, command: DiaryMutation) -> None:
    entry_ids = {command.entry_id}
    if isinstance(command, MoveDish):
        if command.target_entry_id == command.entry_id:
            raise DiaryEditError(400, "same_entry")
        entry_ids.add(command.target_entry_id)
    entries = _lock_entries(session, user.id, entry_ids, lock_metrics=isinstance(command, EditDish))
    source = entries[command.entry_id]
    item = next((dish for dish in source.items if dish.id == command.item_id), None)
    if item is None:
        raise DiaryEditError(409, "dish_changed")

    if isinstance(command, MoveDish):
        _move_dish(session, user, source, entries[command.target_entry_id], item, command)
    else:
        _edit_dish(session, source, item, command)


def _move_dish(
    session: Session, user: User, source: Entry, target: Entry, item: EntryItem, command: MoveDish
) -> None:
    preference = UserSummaryPreferenceRepository(session).get_by_user_id(user.id)
    day_start = preference.nutrition_day_start_hour if preference is not None else 4
    source_day, target_day = (
        resolve_local_summary_date(
            reference_at=entry.occurred_at,
            timezone_name=user.timezone,
            nutrition_day_start_hour=day_start,
        )
        for entry in (source, target)
    )
    if source_day != target_day:
        raise DiaryEditError(400, "different_food_day")
    last_dish = len(source.items) == 1
    if last_dish and not command.confirm_delete:
        raise DiaryEditError(409, "confirm_empty_entry")

    preserve_entry_item_source_facts(source)
    preserve_entry_item_source_facts(target)
    position = max((dish.position for dish in target.items), default=-1) + 1
    item.entry = target
    item.position = position
    session.flush()
    if last_dish:
        # Reload the collection after reparenting so delete-orphan cannot touch the moved dish.
        session.expire(source, ["items"])
        session.delete(source)


def _edit_dish(session: Session, entry: Entry, item: EntryItem, command: EditDish) -> None:
    values = dish_metric_values(item)
    if dish_revision(item, values) != command.revision:
        raise DiaryEditError(409, "dish_changed")

    old_weight = item.quantity if (item.unit or "").strip().lower() in {"g", "г", "гр"} else None
    if old_weight is not None and old_weight <= 0:
        old_weight = None
    overrides = command.per_100g.model_dump(exclude_none=True)
    if any(
        code not in overrides and (old_weight is None or code not in values)
        for code in DIARY_METRICS
    ):
        raise DiaryEditError(422, "missing_per_100g")

    current_metrics = {
        metric.metric.code: metric for metric in item.metrics if metric.metric is not None
    }
    updates = []
    for code in SUPPORTED_NUTRITION_METRIC_CODES:
        if code in overrides:
            value, confidence = overrides[code] * (command.weight / 100), "high"
        elif old_weight is not None and old_weight != command.weight and code in values:
            value = values[code] * (command.weight / old_weight)
            confidence = current_metrics[code].confidence
        else:
            continue
        if not math.isfinite(value):
            raise DiaryEditError(422, "invalid_per_100g")
        updates.append(
            EntryItemMetricValue(code=code, value=round(value, 4), confidence=confidence)
        )

    preserve_entry_item_source_facts(entry)
    if overrides:
        facts = item.source_facts or {}
        manual = set(facts.get("manual_metrics", ())) | overrides.keys()
        item.source_facts = {**facts, "manual_metrics": sorted(manual)}
    item.quantity = command.weight
    item.unit = "g"
    if updates:
        EntryItemMetricRepository(session).upsert_metrics(
            entry_item_id=item.id, metric_values=updates
        )
