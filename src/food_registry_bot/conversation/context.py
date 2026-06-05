from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from food_registry_bot.config import get_settings
from food_registry_bot.db.repositories import (
    EntryRepository,
    UserDietPreferenceRepository,
    UserLLMProfileRepository,
)
from food_registry_bot.nutrition import (
    DailyNutritionGoalProgress,
    DailyNutritionGoalProgressUseCase,
    DailyNutritionGoalSnapshotUseCase,
    DailyNutritionSummaryUseCase,
    DailyWorkoutCalorieCreditUseCase,
    DailyWaterSummaryUseCase,
    resolve_day_bounds_utc,
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


class NutritionCoachWorkoutItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    quantity: Optional[int] = Field(default=None, gt=0)
    unit: Optional[str] = Field(default=None, max_length=32)
    rendered_value: str = Field(min_length=1)


class NutritionCoachWorkoutEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: int = Field(gt=0)
    occurred_at: datetime
    source_text: Optional[str] = None
    metric_values: dict[str, float] = Field(default_factory=dict)
    items: list[NutritionCoachWorkoutItem] = Field(default_factory=list)


class NutritionCoachActiveDiet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)


class NutritionCoachFactualContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date: date
    timezone: str = Field(min_length=1, max_length=64)
    nutrition_day_start_hour: int = Field(ge=0, le=23)
    user_context_comment: Optional[str] = None
    day_totals: dict[str, float]
    goal_progress: dict[str, NutritionCoachMetricProgress]
    active_diets: list[NutritionCoachActiveDiet] = Field(default_factory=list)
    workout_entries: list[NutritionCoachWorkoutEntry] = Field(default_factory=list)
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
    rendered_unit = {"ml": "мл", "g": "г", "min": "мин"}.get(unit, unit)
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
        workout_logging_enabled: bool = False,
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
            calorie_goal_adjustment=int(
                round(
                    DailyWorkoutCalorieCreditUseCase(self._session).run(
                        user_id=user_id,
                        timezone_name=timezone_name,
                        summary_date=summary_date,
                        nutrition_day_start_hour=nutrition_day_start_hour,
                    )
                )
            ),
        )
        workout_entries = []
        active_diets = UserDietPreferenceRepository(self._session).list_enabled_for_user(user_id=user_id)
        if workout_logging_enabled:
            occurred_at_from, occurred_at_to = resolve_day_bounds_utc(
                summary_date=summary_date,
                timezone_name=timezone_name,
                nutrition_day_start_hour=nutrition_day_start_hour,
            )
            workout_entries = self._entry_repository.list_workout_for_user_between(
                user_id=user_id,
                occurred_at_from=occurred_at_from,
                occurred_at_to=occurred_at_to,
            )
        recent_entries = self._entry_repository.list_recent_for_user(user_id=user_id, limit=5)
        encryption_secret = get_settings().personal_api_keys_secret
        try:
            user_context_comment = UserLLMProfileRepository(self._session).get_user_context_comment(
                user_id=user_id,
                encryption_secret=encryption_secret,
            )
        except ValueError:
            user_context_comment = None

        return NutritionCoachFactualContext(
            summary_date=summary_date,
            timezone=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
            user_context_comment=user_context_comment,
            day_totals={
                "calories": nutrition_summary.totals.calories,
                "protein": nutrition_summary.totals.protein,
                "fat": nutrition_summary.totals.fat,
                "carbs": nutrition_summary.totals.carbs,
                "fiber": nutrition_summary.totals.fiber,
                "water": float(water_summary.total_ml),
            },
            goal_progress=_build_metric_progress_map(goal_progress),
            active_diets=[
                NutritionCoachActiveDiet(code=diet.code, name=diet.name)
                for diet in active_diets
            ],
            workout_entries=[
                NutritionCoachWorkoutEntry(
                    entry_id=entry.id,
                    occurred_at=entry.occurred_at,
                    source_text=entry.source_text,
                    metric_values={
                        metric.metric.code: metric.value
                        for item in entry.items
                        for metric in item.metrics
                        if metric.metric is not None
                    },
                    items=[
                        NutritionCoachWorkoutItem(
                            name=item.name,
                            quantity=item.quantity,
                            unit=item.unit,
                            rendered_value=_render_recent_entry_item(item.name, item.quantity, item.unit),
                        )
                        for item in sorted(entry.items, key=lambda current: current.position)
                    ],
                )
                for entry in workout_entries
            ],
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
