from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from food_registry_bot.db.repositories import EntryRepository
from food_registry_bot.diet import DietScoreSummary, summarize_diet_scores
from food_registry_bot.nutrition.daily_summary import DailyNutritionSummaryUseCase, DailyNutritionTotals, resolve_day_bounds_utc, resolve_local_summary_date
from food_registry_bot.nutrition.goals import DailyNutritionGoalSnapshotUseCase
from food_registry_bot.nutrition.water_summary import DailyWaterSummaryUseCase
from food_registry_bot.nutrition.workout_credit import DailyWorkoutCalorieCreditUseCase


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
    diet_score_summaries: list[DietScoreSummary] = Field(default_factory=list)
    average_goal_values: dict[str, float] = Field(default_factory=dict)
    goal_hit_day_counts: dict[str, int] = Field(default_factory=dict)
    goal_applicable_day_counts: dict[str, int] = Field(default_factory=dict)


class PeriodMetricDynamicsRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date_from: date
    summary_date_to: date
    data_day_count: int = Field(ge=0)
    average_value: Optional[float] = None
    average_goal_value: Optional[float] = None


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
        self._goal_snapshot_use_case = DailyNutritionGoalSnapshotUseCase(session)
        self._workout_calorie_credit_use_case = DailyWorkoutCalorieCreditUseCase(session)

    def run(
        self,
        *,
        user_id: int,
        timezone_name: str,
        summary_date_to: date,
        period_day_count: int,
        nutrition_day_start_hour: int = 4,
        workout_logging_enabled: bool = False,
        report_goal_tolerance_percent: int = 10,
    ) -> PeriodReport:
        if period_day_count <= 0:
            raise ValueError("period_day_count must be positive")

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
        nutrition_totals_sum = DailyNutritionTotals()
        water_total_ml = 0
        food_data_day_count = 0
        water_data_day_count = 0
        incomplete_food_day_count = 0
        incomplete_water_day_count = 0
        goal_value_sums = {metric_code: 0.0 for metric_code in ("calories", "protein", "fat", "carbs", "fiber", "water")}
        goal_value_day_counts = {metric_code: 0 for metric_code in ("calories", "protein", "fat", "carbs", "fiber", "water")}
        goal_hit_day_counts = {metric_code: 0 for metric_code in ("calories", "protein", "fat", "carbs", "fiber", "water")}
        goal_applicable_day_counts = {metric_code: 0 for metric_code in ("calories", "protein", "fat", "carbs", "fiber", "water")}

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

            goal_snapshot = self._goal_snapshot_use_case.get_or_create(
                user_id=user_id,
                summary_date=summary_date,
                timezone_name=timezone_name,
                nutrition_day_start_hour=nutrition_day_start_hour,
            )
            calorie_goal_adjustment = 0
            if workout_logging_enabled:
                calorie_goal_adjustment = int(
                    round(
                        self._workout_calorie_credit_use_case.run(
                            user_id=user_id,
                            timezone_name=timezone_name,
                            summary_date=summary_date,
                            nutrition_day_start_hour=nutrition_day_start_hour,
                        )
                    )
                )
            nutrition_metrics = (
                ("calories", nutrition_summary.totals.calories, goal_snapshot.calorie_goal + calorie_goal_adjustment),
                ("protein", nutrition_summary.totals.protein, goal_snapshot.protein_goal),
                ("fat", nutrition_summary.totals.fat, goal_snapshot.fat_goal),
                ("carbs", nutrition_summary.totals.carbs, goal_snapshot.carbs_goal),
                ("fiber", nutrition_summary.totals.fiber, goal_snapshot.fiber_goal),
            )
            if nutrition_summary.included_entry_count > 0 or nutrition_summary.excluded_entry_count > 0:
                for metric_code, _metric_value, goal_value in nutrition_metrics:
                    goal_value_sums[metric_code] += float(goal_value)
                    goal_value_day_counts[metric_code] += 1
            if nutrition_summary.included_entry_count > 0 and nutrition_summary.excluded_entry_count == 0:
                for metric_code, metric_value, goal_value in nutrition_metrics:
                    goal_applicable_day_counts[metric_code] += 1
                    if _is_within_goal_tolerance(
                        metric_value=metric_value,
                        goal_value=goal_value,
                        tolerance_percent=report_goal_tolerance_percent,
                    ):
                        goal_hit_day_counts[metric_code] += 1
            if water_summary.included_entry_count > 0 and water_summary.excluded_entry_count == 0:
                goal_applicable_day_counts["water"] += 1
                if _is_within_goal_tolerance(
                    metric_value=float(water_summary.total_ml),
                    goal_value=goal_snapshot.water_goal,
                    tolerance_percent=report_goal_tolerance_percent,
                ):
                    goal_hit_day_counts["water"] += 1
            if water_summary.included_entry_count > 0 or water_summary.excluded_entry_count > 0:
                goal_value_sums["water"] += float(goal_snapshot.water_goal)
                goal_value_day_counts["water"] += 1

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
        average_goal_values = {
            metric_code: goal_value_sums[metric_code] / goal_value_day_counts[metric_code]
            for metric_code in goal_value_sums
            if goal_value_day_counts[metric_code] > 0
        }
        diet_score_summaries = summarize_diet_scores(
            entries=[
                *self._entry_repository.list_food_for_user_between(
                    user_id=user_id,
                    occurred_at_from=occurred_at_from,
                    occurred_at_to=occurred_at_to,
                ),
                *self._entry_repository.list_water_for_user_between(
                    user_id=user_id,
                    occurred_at_from=occurred_at_from,
                    occurred_at_to=occurred_at_to,
                ),
            ],
            timezone_name=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
        )

        workout_day_count = 0
        workout_entry_count = 0
        if workout_logging_enabled:
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
            diet_score_summaries=diet_score_summaries,
            average_goal_values=average_goal_values,
            goal_hit_day_counts=goal_hit_day_counts,
            goal_applicable_day_counts=goal_applicable_day_counts,
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
        workout_logging_enabled: bool = False,
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
            total_goal_value = 0.0
            data_day_count = 0

            for day_offset in range(subperiod_day_count):
                summary_date = subperiod_date_from + timedelta(days=day_offset)
                goal_snapshot = self._goal_snapshot_use_case.get_or_create(
                    user_id=user_id,
                    summary_date=summary_date,
                    timezone_name=timezone_name,
                    nutrition_day_start_hour=nutrition_day_start_hour,
                )
                calorie_goal_adjustment = 0
                if workout_logging_enabled and metric_code == "calories":
                    calorie_goal_adjustment = int(
                        round(
                            self._workout_calorie_credit_use_case.run(
                                user_id=user_id,
                                timezone_name=timezone_name,
                                summary_date=summary_date,
                                nutrition_day_start_hour=nutrition_day_start_hour,
                            )
                        )
                    )
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
                    total_goal_value += float(goal_snapshot.water_goal)
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
                if metric_code == "calories":
                    total_goal_value += float(goal_snapshot.calorie_goal + calorie_goal_adjustment)
                else:
                    total_goal_value += float(getattr(goal_snapshot, f"{metric_code}_goal"))
                data_day_count += 1

            average_value = None
            average_goal_value = None
            if data_day_count > 0:
                average_value = total_value / data_day_count
                average_goal_value = total_goal_value / data_day_count
            rows.append(
                PeriodMetricDynamicsRow(
                    summary_date_from=subperiod_date_from,
                    summary_date_to=subperiod_date_to,
                    data_day_count=data_day_count,
                    average_value=average_value,
                    average_goal_value=average_goal_value,
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


def _is_within_goal_tolerance(*, metric_value: float, goal_value: int, tolerance_percent: int) -> bool:
    tolerance_delta = goal_value * tolerance_percent / 100
    lower_bound = goal_value - tolerance_delta
    upper_bound = goal_value + tolerance_delta
    return lower_bound <= metric_value <= upper_bound
