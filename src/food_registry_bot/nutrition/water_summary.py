from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from food_registry_bot.db.models import Entry, EntryType
from food_registry_bot.db.repositories import EntryRepository
from food_registry_bot.nutrition.daily_summary import resolve_day_bounds_utc
from food_registry_bot.nutrition.beverage import beverage_volume, is_beverage, is_plain_water


class DailyWaterSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date: date
    timezone: str = Field(min_length=1, max_length=64)
    total_ml: int = Field(ge=0)
    fluid_total_ml: int = Field(default=0, ge=0)
    fluid_has_data: bool = False
    fluid_is_complete: bool = True
    included_entry_count: int = Field(ge=0)
    excluded_entry_count: int = Field(ge=0)
    is_complete: bool

    @model_validator(mode="after")
    def validate_completeness_flag(self) -> "DailyWaterSummary":
        if self.is_complete != (self.excluded_entry_count == 0):
            raise ValueError("is_complete must match excluded_entry_count")
        return self


class DailyWaterSummaryUseCase:
    def __init__(self, session: Session) -> None:
        self._entry_repository = EntryRepository(session)

    def run(
        self,
        *,
        user_id: int,
        timezone_name: str,
        summary_date: date,
        nutrition_day_start_hour: int = 4,
    ) -> DailyWaterSummary:
        occurred_at_from, occurred_at_to = resolve_day_bounds_utc(
            summary_date=summary_date,
            timezone_name=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
        )
        entries = self._entry_repository.list_diary_for_user_between(
            user_id=user_id,
            occurred_at_from=occurred_at_from,
            occurred_at_to=occurred_at_to,
        )

        return summarize_water_entries(entries, summary_date=summary_date, timezone_name=timezone_name)


def summarize_water_entries(
    entries: list[Entry], *, summary_date: date, timezone_name: str,
) -> DailyWaterSummary:
    total_ml = fluid_total_ml = included = excluded = 0
    fluid_has_data = False
    fluid_is_complete = True
    for entry in entries:
        beverages = [beverage_volume(item) for item in entry.items if is_beverage(item)]
        fluid_total_ml += sum(value or 0 for value in beverages)
        fluid_has_data |= bool(beverages) or entry.entry_type is EntryType.WATER
        fluid_is_complete &= all(value is not None for value in beverages)
        items = [item for item in entry.items if is_plain_water(item)]
        if entry.entry_type is EntryType.WATER and (not items or len(items) != len(entry.items)):
            excluded += 1
            fluid_is_complete = False
            continue
        if not items:
            continue
        amounts = [beverage_volume(item) for item in items]
        if any(amount is None for amount in amounts):
            excluded += 1
        else:
            included += 1
            total_ml += sum(amount for amount in amounts if amount is not None)
    return DailyWaterSummary(
        summary_date=summary_date, timezone=timezone_name, total_ml=total_ml,
        fluid_total_ml=fluid_total_ml, included_entry_count=included,
        fluid_has_data=fluid_has_data, fluid_is_complete=fluid_is_complete,
        excluded_entry_count=excluded, is_complete=excluded == 0,
    )
