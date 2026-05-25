from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from food_registry_bot.db.repositories import EntryRepository
from food_registry_bot.nutrition.daily_summary import DailyNutritionSummaryUseCase, DailyNutritionTotals, resolve_day_bounds_utc, resolve_local_summary_date
from food_registry_bot.nutrition.water_summary import DailyWaterSummaryUseCase


class PeriodReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date_from: date
    summary_date_to: date
    period_day_count: int = Field(gt=0)
    average_nutrition_totals: DailyNutritionTotals
    average_water_ml: float = Field(ge=0)
    food_data_day_count: int = Field(ge=0)
    water_data_day_count: int = Field(ge=0)
    workout_day_count: int = Field(ge=0)
    workout_entry_count: int = Field(ge=0)
    incomplete_food_day_count: int = Field(ge=0)
    incomplete_water_day_count: int = Field(ge=0)


class PeriodReportUseCase:
    def __init__(self, session: Session) -> None:
        self._nutrition_summary_use_case = DailyNutritionSummaryUseCase(session)
        self._water_summary_use_case = DailyWaterSummaryUseCase(session)
        self._entry_repository = EntryRepository(session)

    def run(
        self,
        *,
        user_id: int,
        timezone_name: str,
        summary_date_to: date,
        period_day_count: int,
        nutrition_day_start_hour: int = 4,
        workout_logging_enabled: bool = False,
    ) -> PeriodReport:
        if period_day_count <= 0:
            raise ValueError("period_day_count must be positive")

        summary_date_from = summary_date_to - timedelta(days=period_day_count - 1)
        nutrition_totals_sum = DailyNutritionTotals()
        water_total_ml = 0
        food_data_day_count = 0
        water_data_day_count = 0
        incomplete_food_day_count = 0
        incomplete_water_day_count = 0

        for day_offset in range(period_day_count):
            summary_date = summary_date_from + timedelta(days=day_offset)
            nutrition_summary = self._nutrition_summary_use_case.run(
                user_id=user_id,
                timezone_name=timezone_name,
                summary_date=summary_date,
                nutrition_day_start_hour=nutrition_day_start_hour,
            )
            if nutrition_summary.included_entry_count > 0 or nutrition_summary.excluded_entry_count > 0:
                food_data_day_count += 1
                nutrition_totals_sum = DailyNutritionTotals(
                    calories=nutrition_totals_sum.calories + nutrition_summary.totals.calories,
                    protein=nutrition_totals_sum.protein + nutrition_summary.totals.protein,
                    fat=nutrition_totals_sum.fat + nutrition_summary.totals.fat,
                    carbs=nutrition_totals_sum.carbs + nutrition_summary.totals.carbs,
                    fiber=nutrition_totals_sum.fiber + nutrition_summary.totals.fiber,
                )
                if not nutrition_summary.is_complete:
                    incomplete_food_day_count += 1

            water_summary = self._water_summary_use_case.run(
                user_id=user_id,
                timezone_name=timezone_name,
                summary_date=summary_date,
                nutrition_day_start_hour=nutrition_day_start_hour,
            )
            if water_summary.included_entry_count > 0 or water_summary.excluded_entry_count > 0:
                water_data_day_count += 1
                water_total_ml += water_summary.total_ml
                if not water_summary.is_complete:
                    incomplete_water_day_count += 1

        average_nutrition_totals = DailyNutritionTotals()
        if food_data_day_count > 0:
            average_nutrition_totals = DailyNutritionTotals(
                calories=nutrition_totals_sum.calories / food_data_day_count,
                protein=nutrition_totals_sum.protein / food_data_day_count,
                fat=nutrition_totals_sum.fat / food_data_day_count,
                carbs=nutrition_totals_sum.carbs / food_data_day_count,
                fiber=nutrition_totals_sum.fiber / food_data_day_count,
            )

        average_water_ml = 0.0
        if water_data_day_count > 0:
            average_water_ml = water_total_ml / water_data_day_count

        workout_day_count = 0
        workout_entry_count = 0
        if workout_logging_enabled:
            occurred_at_from, _ignored = resolve_day_bounds_utc(
                summary_date=summary_date_from,
                timezone_name=timezone_name,
                nutrition_day_start_hour=nutrition_day_start_hour,
            )
            _ignored_from, occurred_at_to = resolve_day_bounds_utc(
                summary_date=summary_date_to + timedelta(days=1),
                timezone_name=timezone_name,
                nutrition_day_start_hour=nutrition_day_start_hour,
            )
            workout_entries = self._entry_repository.list_workout_for_user_between(
                user_id=user_id,
                occurred_at_from=occurred_at_from,
                occurred_at_to=occurred_at_to,
            )
            workout_entry_count = len(workout_entries)
            workout_day_count = len(
                {
                    resolve_local_summary_date(
                        reference_at=entry.occurred_at,
                        timezone_name=timezone_name,
                        nutrition_day_start_hour=nutrition_day_start_hour,
                    )
                    for entry in workout_entries
                }
            )

        return PeriodReport(
            summary_date_from=summary_date_from,
            summary_date_to=summary_date_to,
            period_day_count=period_day_count,
            average_nutrition_totals=average_nutrition_totals,
            average_water_ml=average_water_ml,
            food_data_day_count=food_data_day_count,
            water_data_day_count=water_data_day_count,
            workout_day_count=workout_day_count,
            workout_entry_count=workout_entry_count,
            incomplete_food_day_count=incomplete_food_day_count,
            incomplete_water_day_count=incomplete_water_day_count,
        )
