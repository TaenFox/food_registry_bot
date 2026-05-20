from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from food_registry_bot.db.models import DailyGoalSnapshot
from food_registry_bot.nutrition.daily_summary import DailyNutritionSummary
from food_registry_bot.nutrition.water_summary import DailyWaterSummary


class MetricGoalProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_code: str = Field(min_length=1)
    consumed_value: float = Field(ge=0)
    goal_value: int = Field(gt=0)
    remaining_value: float
    is_over_goal: bool


class DailyNutritionGoalProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date: date
    calories: MetricGoalProgress
    protein: MetricGoalProgress
    fat: MetricGoalProgress
    carbs: MetricGoalProgress
    fiber: MetricGoalProgress
    water: MetricGoalProgress


class DailyNutritionGoalProgressUseCase:
    def build(
        self,
        *,
        summary: DailyNutritionSummary,
        water_summary: DailyWaterSummary,
        snapshot: DailyGoalSnapshot,
    ) -> DailyNutritionGoalProgress:
        return DailyNutritionGoalProgress(
            summary_date=summary.summary_date,
            calories=self._build_metric_progress(
                metric_code="calories",
                consumed_value=summary.totals.calories,
                goal_value=snapshot.calorie_goal,
            ),
            protein=self._build_metric_progress(
                metric_code="protein",
                consumed_value=summary.totals.protein,
                goal_value=snapshot.protein_goal,
            ),
            fat=self._build_metric_progress(
                metric_code="fat",
                consumed_value=summary.totals.fat,
                goal_value=snapshot.fat_goal,
            ),
            carbs=self._build_metric_progress(
                metric_code="carbs",
                consumed_value=summary.totals.carbs,
                goal_value=snapshot.carbs_goal,
            ),
            fiber=self._build_metric_progress(
                metric_code="fiber",
                consumed_value=summary.totals.fiber,
                goal_value=snapshot.fiber_goal,
            ),
            water=self._build_metric_progress(
                metric_code="water",
                consumed_value=water_summary.total_ml,
                goal_value=snapshot.water_goal,
            ),
        )

    @staticmethod
    def _build_metric_progress(
        *,
        metric_code: str,
        consumed_value: float,
        goal_value: int,
    ) -> MetricGoalProgress:
        remaining_value = goal_value - consumed_value
        return MetricGoalProgress(
            metric_code=metric_code,
            consumed_value=consumed_value,
            goal_value=goal_value,
            remaining_value=remaining_value,
            is_over_goal=remaining_value < 0,
        )
