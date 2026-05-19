from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from food_registry_bot.db.models import Entry
from food_registry_bot.db.repositories import EntryRepository
from food_registry_bot.nutrition.daily_summary import resolve_day_bounds_utc


class DailyWaterSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date: date
    timezone: str = Field(min_length=1, max_length=64)
    total_ml: int = Field(ge=0)
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
        entries = self._entry_repository.list_water_for_user_between(
            user_id=user_id,
            occurred_at_from=occurred_at_from,
            occurred_at_to=occurred_at_to,
        )

        total_ml = 0
        included_entry_count = 0
        excluded_entry_count = 0
        for entry in entries:
            entry_total_ml = self._resolve_entry_total_ml(entry)
            if entry_total_ml is None:
                excluded_entry_count += 1
                continue
            total_ml += entry_total_ml
            included_entry_count += 1

        return DailyWaterSummary(
            summary_date=summary_date,
            timezone=timezone_name,
            total_ml=total_ml,
            included_entry_count=included_entry_count,
            excluded_entry_count=excluded_entry_count,
            is_complete=excluded_entry_count == 0,
        )

    @staticmethod
    def _resolve_entry_total_ml(entry: Entry) -> int | None:
        if not entry.items:
            return None

        total_ml = 0
        for item in entry.items:
            if item.name != "water":
                return None
            if item.unit != "ml":
                return None
            if item.quantity is None or item.quantity <= 0:
                return None
            total_ml += item.quantity
        return total_ml
