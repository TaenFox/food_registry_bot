from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from food_registry_bot.db.models import DailyGoalSnapshot
from food_registry_bot.db.repositories import (
    DailyGoalSnapshotRepository,
    UserGoalPreferenceRepository,
)


class DailyNutritionGoalSnapshotUseCase:
    def __init__(self, session: Session) -> None:
        self._snapshot_repository = DailyGoalSnapshotRepository(session)
        self._preference_repository = UserGoalPreferenceRepository(session)

    def get_or_create(
        self,
        *,
        user_id: int,
        summary_date: date,
        timezone_name: str,
        nutrition_day_start_hour: int,
    ) -> DailyGoalSnapshot:
        snapshot = self._snapshot_repository.get_by_user_id_and_date(
            user_id=user_id,
            summary_date=summary_date,
        )
        if snapshot is not None:
            return snapshot

        preference, _created = self._preference_repository.get_or_create(user_id=user_id)
        return self._snapshot_repository.create(
            user_id=user_id,
            summary_date=summary_date,
            timezone_name=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
            calorie_goal=preference.calorie_goal,
            protein_goal=preference.protein_goal,
            fat_goal=preference.fat_goal,
            carbs_goal=preference.carbs_goal,
        )
