from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from food_registry_bot.db.models import Entry
from food_registry_bot.db.repositories import EntryRepository
from food_registry_bot.nutrition.contract import SUPPORTED_NUTRITION_METRIC_CODES

NUTRITION_DAY_START_HOUR = 4


class DailyNutritionTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calories: float = Field(default=0.0, ge=0)
    protein: float = Field(default=0.0, ge=0)
    fat: float = Field(default=0.0, ge=0)
    carbs: float = Field(default=0.0, ge=0)


class DailyNutritionItemSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    entry_item_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=255)
    quantity: Optional[int] = Field(default=None, gt=0)
    unit: Optional[str] = Field(default=None, max_length=32)
    totals: DailyNutritionTotals


class DailyNutritionEntrySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: int = Field(gt=0)
    occurred_at: datetime
    meal_type: Optional[str] = Field(default=None, max_length=32)
    items: list[DailyNutritionItemSummary] = Field(min_length=1)
    totals: DailyNutritionTotals


class DailyNutritionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date: date
    timezone: str = Field(min_length=1, max_length=64)
    entries: list[DailyNutritionEntrySummary] = Field(default_factory=list)
    totals: DailyNutritionTotals
    included_entry_count: int = Field(ge=0)
    excluded_entry_count: int = Field(ge=0)
    is_complete: bool

    @model_validator(mode="after")
    def validate_completeness_flag(self) -> "DailyNutritionSummary":
        if self.is_complete != (self.excluded_entry_count == 0):
            raise ValueError("is_complete must match excluded_entry_count")
        if self.included_entry_count != len(self.entries):
            raise ValueError("included_entry_count must match entries length")
        return self


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def resolve_local_summary_date(*, reference_at: datetime, timezone_name: str) -> date:
    user_timezone = ZoneInfo(timezone_name)
    local_reference_at = _normalize_datetime(reference_at).astimezone(user_timezone)
    nutrition_day_anchor = local_reference_at - timedelta(hours=NUTRITION_DAY_START_HOUR)
    return nutrition_day_anchor.date()


def resolve_day_bounds_utc(*, summary_date: date, timezone_name: str) -> tuple[datetime, datetime]:
    user_timezone = ZoneInfo(timezone_name)
    start_local = datetime.combine(
        summary_date,
        time(hour=NUTRITION_DAY_START_HOUR),
        tzinfo=user_timezone,
    )
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _empty_totals() -> DailyNutritionTotals:
    return DailyNutritionTotals()


def _merge_totals(left: DailyNutritionTotals, right: DailyNutritionTotals) -> DailyNutritionTotals:
    return DailyNutritionTotals(
        calories=left.calories + right.calories,
        protein=left.protein + right.protein,
        fat=left.fat + right.fat,
        carbs=left.carbs + right.carbs,
    )


class DailyNutritionSummaryUseCase:
    def __init__(self, session: Session) -> None:
        self._entry_repository = EntryRepository(session)
        self._required_metric_codes = set(SUPPORTED_NUTRITION_METRIC_CODES)

    def run(
        self,
        *,
        user_id: int,
        timezone_name: str,
        summary_date: date,
    ) -> DailyNutritionSummary:
        occurred_at_from, occurred_at_to = resolve_day_bounds_utc(
            summary_date=summary_date,
            timezone_name=timezone_name,
        )
        entries = self._entry_repository.list_food_for_user_between(
            user_id=user_id,
            occurred_at_from=occurred_at_from,
            occurred_at_to=occurred_at_to,
        )

        entry_summaries: list[DailyNutritionEntrySummary] = []
        day_totals = _empty_totals()
        excluded_entry_count = 0
        for entry in entries:
            entry_summary = self._build_entry_summary(entry)
            if entry_summary is None:
                excluded_entry_count += 1
                continue

            entry_summaries.append(entry_summary)
            day_totals = _merge_totals(day_totals, entry_summary.totals)

        return DailyNutritionSummary(
            summary_date=summary_date,
            timezone=timezone_name,
            entries=entry_summaries,
            totals=day_totals,
            included_entry_count=len(entry_summaries),
            excluded_entry_count=excluded_entry_count,
            is_complete=excluded_entry_count == 0,
        )

    def _build_entry_summary(self, entry: Entry) -> DailyNutritionEntrySummary | None:
        item_summaries: list[DailyNutritionItemSummary] = []
        entry_totals = _empty_totals()
        for item in sorted(entry.items, key=lambda current: current.position):
            metric_values = {
                metric.metric.code: metric.value
                for metric in item.metrics
                if metric.metric is not None
            }
            if set(metric_values) != self._required_metric_codes:
                return None

            item_totals = DailyNutritionTotals(
                calories=metric_values["calories"],
                protein=metric_values["protein"],
                fat=metric_values["fat"],
                carbs=metric_values["carbs"],
            )
            item_summaries.append(
                DailyNutritionItemSummary(
                    entry_item_id=item.id,
                    name=item.name,
                    quantity=item.quantity,
                    unit=item.unit,
                    totals=item_totals,
                )
            )
            entry_totals = _merge_totals(entry_totals, item_totals)

        if not item_summaries:
            return None

        return DailyNutritionEntrySummary(
            entry_id=entry.id,
            occurred_at=_normalize_datetime(entry.occurred_at),
            meal_type=entry.meal_type.value if entry.meal_type is not None else None,
            items=item_summaries,
            totals=entry_totals,
        )
