from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from food_registry_bot.db.repositories import EntryRepository
from food_registry_bot.nutrition.daily_summary import resolve_day_bounds_utc


def resolve_workout_metric_value(entry, metric_code: str) -> float:
    for item in entry.items:
        for metric in item.metrics:
            if metric.metric is not None and metric.metric.code == metric_code:
                return metric.value
    return 0.0


def calculate_default_workout_calorie_credit(workout_calories: float) -> float:
    if workout_calories < 200:
        return 0.0
    return float(int(workout_calories // 200) * 100)


class DailyWorkoutCalorieCreditUseCase:
    def __init__(self, session: Session) -> None:
        self._entry_repository = EntryRepository(session)

    def run(
        self,
        *,
        user_id: int,
        timezone_name: str,
        summary_date: date,
        nutrition_day_start_hour: int = 4,
    ) -> float:
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
        return sum(resolve_workout_metric_value(entry, "workout_calorie_credit") for entry in workout_entries)
