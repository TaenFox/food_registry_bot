from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

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


class PeriodMetricDynamicsRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date_from: date
    summary_date_to: date
    data_day_count: int = Field(ge=0)
    average_value: Optional[float] = None


class PeriodMetricDynamics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date_from: date
    summary_date_to: date
    period_day_count: int = Field(gt=0)
    metric_code: str = Field(min_length=1)
    subperiod_day_count: int = Field(gt=0)
    rows: list[PeriodMetricDynamicsRow] = Field(min_length=1)


class PeriodNoticeableEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: int = Field(gt=0)
    occurred_at: date
    title: str = Field(min_length=1)
    metric_value: float = Field(ge=0)


class PeriodNoticeableEntries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date_from: date
    summary_date_to: date
    period_day_count: int = Field(gt=0)
    metric_code: str = Field(min_length=1)
    percentile: int = Field(ge=0, le=100)
    entries: list[PeriodNoticeableEntry] = Field(default_factory=list)


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

    def build_metric_dynamics(
        self,
        *,
        user_id: int,
        timezone_name: str,
        summary_date_to: date,
        period_day_count: int,
        metric_code: str,
        nutrition_day_start_hour: int = 4,
        subperiod_day_count: int = 4,
    ) -> PeriodMetricDynamics:
        if period_day_count <= 0:
            raise ValueError("period_day_count must be positive")
        if subperiod_day_count <= 0:
            raise ValueError("subperiod_day_count must be positive")
        if period_day_count % subperiod_day_count != 0:
            raise ValueError("period_day_count must be divisible by subperiod_day_count")

        summary_date_from = summary_date_to - timedelta(days=period_day_count - 1)
        rows: list[PeriodMetricDynamicsRow] = []

        for subperiod_start_offset in range(0, period_day_count, subperiod_day_count):
            subperiod_date_from = summary_date_from + timedelta(days=subperiod_start_offset)
            subperiod_date_to = subperiod_date_from + timedelta(days=subperiod_day_count - 1)
            total_value = 0.0
            data_day_count = 0

            for day_offset in range(subperiod_day_count):
                summary_date = subperiod_date_from + timedelta(days=day_offset)
                if metric_code == "water":
                    water_summary = self._water_summary_use_case.run(
                        user_id=user_id,
                        timezone_name=timezone_name,
                        summary_date=summary_date,
                        nutrition_day_start_hour=nutrition_day_start_hour,
                    )
                    if water_summary.included_entry_count == 0 and water_summary.excluded_entry_count == 0:
                        continue
                    total_value += float(water_summary.total_ml)
                    data_day_count += 1
                    continue

                nutrition_summary = self._nutrition_summary_use_case.run(
                    user_id=user_id,
                    timezone_name=timezone_name,
                    summary_date=summary_date,
                    nutrition_day_start_hour=nutrition_day_start_hour,
                )
                if nutrition_summary.included_entry_count == 0 and nutrition_summary.excluded_entry_count == 0:
                    continue
                total_value += float(getattr(nutrition_summary.totals, metric_code))
                data_day_count += 1

            average_value = None
            if data_day_count > 0:
                average_value = total_value / data_day_count
            rows.append(
                PeriodMetricDynamicsRow(
                    summary_date_from=subperiod_date_from,
                    summary_date_to=subperiod_date_to,
                    data_day_count=data_day_count,
                    average_value=average_value,
                )
            )

        return PeriodMetricDynamics(
            summary_date_from=summary_date_from,
            summary_date_to=summary_date_to,
            period_day_count=period_day_count,
            metric_code=metric_code,
            subperiod_day_count=subperiod_day_count,
            rows=rows,
        )

    def build_noticeable_entries(
        self,
        *,
        user_id: int,
        timezone_name: str,
        summary_date_to: date,
        period_day_count: int,
        metric_code: str,
        nutrition_day_start_hour: int = 4,
        percentile: int = 80,
        limit: int = 10,
    ) -> PeriodNoticeableEntries:
        if period_day_count <= 0:
            raise ValueError("period_day_count must be positive")
        if limit <= 0:
            raise ValueError("limit must be positive")
        summary_date_from = summary_date_to - timedelta(days=period_day_count - 1)
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
        entries = self._entry_repository.list_food_for_user_between(
            user_id=user_id,
            occurred_at_from=occurred_at_from,
            occurred_at_to=occurred_at_to,
        )
        candidates: list[PeriodNoticeableEntry] = []
        metric_values: list[float] = []
        for entry in entries:
            entry_total_value = 0.0
            has_metric = False
            for item in entry.items:
                metric_map = {
                    metric.metric.code: metric.value
                    for metric in item.metrics
                    if metric.metric is not None
                }
                metric_value = metric_map.get(metric_code)
                if metric_value is None:
                    continue
                entry_total_value += float(metric_value)
                has_metric = True
            if not has_metric:
                continue
            metric_values.append(entry_total_value)
            title_parts = [item.name for item in sorted(entry.items, key=lambda current: current.position) if item.name]
            candidates.append(
                PeriodNoticeableEntry(
                    entry_id=entry.id,
                    occurred_at=resolve_local_summary_date(
                        reference_at=entry.occurred_at,
                        timezone_name=timezone_name,
                        nutrition_day_start_hour=nutrition_day_start_hour,
                    ),
                    title=", ".join(title_parts[:3]) if title_parts else "запись",
                    metric_value=entry_total_value,
                )
            )

        threshold = _percentile_threshold(metric_values, percentile)
        noticeable_entries = sorted(
            [entry for entry in candidates if entry.metric_value >= threshold],
            key=lambda current: (current.metric_value, current.entry_id),
            reverse=True,
        )[:limit]
        return PeriodNoticeableEntries(
            summary_date_from=summary_date_from,
            summary_date_to=summary_date_to,
            period_day_count=period_day_count,
            metric_code=metric_code,
            percentile=percentile,
            entries=noticeable_entries,
        )


def _percentile_threshold(values: list[float], percentile: int) -> float:
    if not values:
        return float("inf")
    sorted_values = sorted(values)
    index = max(int(len(sorted_values) * percentile / 100) - 1, 0)
    index = min(index, len(sorted_values) - 1)
    return sorted_values[index]
