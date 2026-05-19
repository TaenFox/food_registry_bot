from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from food_registry_bot.db.models import DailyGoalSnapshot
from food_registry_bot.nutrition.daily_summary import DailyNutritionSummary


class DailyCalorieProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_date: date
    consumed_calories: float = Field(ge=0)
    goal_calories: int = Field(gt=0)
    remaining_calories: float
    is_over_goal: bool


class DailyCalorieProgressUseCase:
    def build(
        self,
        *,
        summary: DailyNutritionSummary,
        snapshot: DailyGoalSnapshot | None,
    ) -> DailyCalorieProgress | None:
        if snapshot is None:
            return None

        remaining_calories = snapshot.calorie_goal - summary.totals.calories
        return DailyCalorieProgress(
            summary_date=summary.summary_date,
            consumed_calories=summary.totals.calories,
            goal_calories=snapshot.calorie_goal,
            remaining_calories=remaining_calories,
            is_over_goal=remaining_calories < 0,
        )
