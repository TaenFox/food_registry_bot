from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from food_registry_bot.db.repositories import EntryRepository
from food_registry_bot.nutrition import (
    DailyNutritionGoalProgress,
    DailyNutritionGoalProgressUseCase,
    DailyNutritionGoalSnapshotUseCase,
    DailyNutritionSummaryUseCase,
    DailyWaterSummaryUseCase,
    resolve_local_summary_date,
)


class NutritionCoachMetricProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consumed_value: float = Field(ge=0)
    goal_value: int = Field(gt=0)
    remaining_value: float
    is_over_goal: bool


class NutritionCoachRecentEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_type: str = Field(min_length=1, max_length=32)
    occurred_at: datetime
    rendered_items: list[str] = Field(default_factory=list)


class NutritionCoachConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=32)
    content: str = Field(min_length=1)
    created_at: datetime


class NutritionCoachFactualContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date: date
    timezone: str = Field(min_length=1, max_length=64)
    nutrition_day_start_hour: int = Field(ge=0, le=23)
    day_totals: dict[str, float]
    goal_progress: dict[str, NutritionCoachMetricProgress]
    recent_entries: list[NutritionCoachRecentEntry] = Field(default_factory=list)
    nutrition_summary_is_complete: bool
    excluded_food_entry_count: int = Field(ge=0)
    water_summary_is_complete: bool
    excluded_water_entry_count: int = Field(ge=0)


def _build_metric_progress_map(progress: DailyNutritionGoalProgress) -> dict[str, NutritionCoachMetricProgress]:
    return {
        metric_code: NutritionCoachMetricProgress(
            consumed_value=getattr(progress, metric_code).consumed_value,
            goal_value=getattr(progress, metric_code).goal_value,
            remaining_value=getattr(progress, metric_code).remaining_value,
            is_over_goal=getattr(progress, metric_code).is_over_goal,
        )
        for metric_code in ("calories", "protein", "fat", "carbs", "fiber", "water")
    }


def _render_recent_entry_item(name: str, quantity: int | None, unit: str | None) -> str:
    rendered_name = "вода" if name == "water" else name
    rendered_unit = {"ml": "мл", "g": "г"}.get(unit, unit)
    if quantity is None:
        return rendered_name
    if rendered_unit is None:
        return f"{rendered_name}: {quantity}"
    return f"{rendered_name}: {quantity} {rendered_unit}"


class NutritionCoachContextBuilder:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._entry_repository = EntryRepository(session)

    def build(
        self,
        *,
        user_id: int,
        timezone_name: str,
        nutrition_day_start_hour: int,
        reference_at: datetime,
    ) -> NutritionCoachFactualContext:
        summary_date = resolve_local_summary_date(
            reference_at=reference_at,
            timezone_name=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
        )
        nutrition_summary = DailyNutritionSummaryUseCase(self._session).run(
            user_id=user_id,
            timezone_name=timezone_name,
            summary_date=summary_date,
            nutrition_day_start_hour=nutrition_day_start_hour,
        )
        water_summary = DailyWaterSummaryUseCase(self._session).run(
            user_id=user_id,
            timezone_name=timezone_name,
            summary_date=summary_date,
            nutrition_day_start_hour=nutrition_day_start_hour,
        )
        goal_snapshot = DailyNutritionGoalSnapshotUseCase(self._session).get_or_create(
            user_id=user_id,
            summary_date=summary_date,
            timezone_name=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
        )
        goal_progress = DailyNutritionGoalProgressUseCase().build(
            summary=nutrition_summary,
            water_summary=water_summary,
            snapshot=goal_snapshot,
        )
        recent_entries = self._entry_repository.list_recent_for_user(user_id=user_id, limit=5)

        return NutritionCoachFactualContext(
            summary_date=summary_date,
            timezone=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
            day_totals={
                "calories": nutrition_summary.totals.calories,
                "protein": nutrition_summary.totals.protein,
                "fat": nutrition_summary.totals.fat,
                "carbs": nutrition_summary.totals.carbs,
                "fiber": nutrition_summary.totals.fiber,
                "water": float(water_summary.total_ml),
            },
            goal_progress=_build_metric_progress_map(goal_progress),
            recent_entries=[
                NutritionCoachRecentEntry(
                    entry_type=entry.entry_type.value,
                    occurred_at=entry.occurred_at,
                    rendered_items=[
                        _render_recent_entry_item(item.name, item.quantity, item.unit)
                        for item in sorted(entry.items, key=lambda current: current.position)
                    ],
                )
                for entry in recent_entries
            ],
            nutrition_summary_is_complete=nutrition_summary.is_complete,
            excluded_food_entry_count=nutrition_summary.excluded_entry_count,
            water_summary_is_complete=water_summary.is_complete,
            excluded_water_entry_count=water_summary.excluded_entry_count,
        )
