from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.access import can_access_bot
from food_registry_bot.db.models import Entry, User
from food_registry_bot.db.repositories import (
    DailyGoalSnapshotRepository,
    EntryRepository,
    UserRepository,
    UserSummaryPreferenceRepository,
)
from food_registry_bot.nutrition.daily_summary import (
    DailyNutritionSummaryUseCase,
    resolve_day_bounds_utc,
    resolve_local_summary_date,
)
from food_registry_bot.nutrition.workout_credit import DailyWorkoutCalorieCreditUseCase

MINI_APP_PORT = 8081
INIT_DATA_MAX_AGE = timedelta(hours=1)
INIT_DATA_CLOCK_SKEW = timedelta(seconds=30)
GOAL_FIELDS = {
    "calories": "calorie_goal",
    "protein": "protein_goal",
    "fat": "fat_goal",
    "carbs": "carbs_goal",
}


def meal_data(entry: Entry, user_timezone: ZoneInfo) -> dict:
    dishes = [
        {
            "name": item.name,
            "calories": next(
                (
                    metric.value
                    for metric in item.metrics
                    if metric.metric is not None and metric.metric.code == "calories"
                ),
                None,
            ),
        }
        for item in sorted(entry.items, key=lambda item: item.position)
    ]
    known_calories = [dish["calories"] for dish in dishes if dish["calories"] is not None]
    occurred_at = entry.occurred_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return {
        "time": occurred_at.astimezone(user_timezone).strftime("%H:%M"),
        "dishes": dishes,
        "calories": sum(known_calories) if known_calories else None,
        "complete": bool(dishes) and len(known_calories) == len(dishes),
    }


def read_diary(session: Session, user: User, now: datetime) -> dict:
    preference = UserSummaryPreferenceRepository(session).get_by_user_id(user.id)
    start_hour = preference.nutrition_day_start_hour if preference is not None else 4
    tolerance = preference.report_goal_tolerance_percent if preference is not None else 10
    metrics = [
        code for code in GOAL_FIELDS if preference is None or getattr(preference, f"show_{code}")
    ]
    today = resolve_local_summary_date(
        reference_at=now, timezone_name=user.timezone, nutrition_day_start_hour=start_hour
    )
    user_timezone = ZoneInfo(user.timezone)
    repository = EntryRepository(session)
    snapshots = DailyGoalSnapshotRepository(session)
    summary_use_case = DailyNutritionSummaryUseCase(session)
    workout_credit = DailyWorkoutCalorieCreditUseCase(session)
    days = []
    for days_ago in range(4, -1, -1):
        day = today - timedelta(days=days_ago)
        start, end = resolve_day_bounds_utc(
            summary_date=day, timezone_name=user.timezone, nutrition_day_start_hour=start_hour
        )
        entries = repository.list_food_for_user_between(
            user_id=user.id, occurred_at_from=start, occurred_at_to=end
        )
        summary = summary_use_case.summarize_entries(
            entries, summary_date=day, timezone_name=user.timezone
        )
        snapshot = snapshots.get_by_user_id_and_date(user_id=user.id, summary_date=day)
        goals = {code: getattr(snapshot, GOAL_FIELDS[code], None) for code in metrics}
        if goals.get("calories") and user.workout_logging_enabled:
            goals["calories"] += round(
                workout_credit.run(
                    user_id=user.id,
                    timezone_name=user.timezone,
                    summary_date=day,
                    nutrition_day_start_hour=start_hour,
                )
            )

        observations = {}
        for code in metrics:
            value = getattr(summary.totals, code) if summary.included_entry_count else None
            goal = goals[code] or None
            observations[code] = {
                "value": value,
                "goal": goal,
                "percent": value / goal * 100 if value is not None and goal else None,
                "lower": goal * (1 - tolerance / 100) if goal else None,
                "upper": goal * (1 + tolerance / 100) if goal else None,
            }

        days.append(
            {
                "date": day.isoformat(),
                "current": day == today,
                "complete": summary.is_complete,
                "metrics": observations,
                "meals": [meal_data(entry, user_timezone) for entry in reversed(entries)],
            }
        )

    return {"metrics": metrics, "tolerance": tolerance, "days": days}


def create_diary_app(
    session_factory: sessionmaker[Session], *, token: str, admin_user_ids: tuple[int, ...]
) -> web.Application:
    def load(telegram_user_id: int, now: datetime) -> tuple[int, dict]:
        with session_factory() as session:
            if not can_access_bot(session, telegram_user_id, admin_user_ids):
                return 403, {"error": "access_denied"}
            user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
            if user is None:
                return 404, {"error": "profile_missing"}
            return 200, read_diary(session, user, now)

    async def diary(request: web.Request) -> web.Response:
        scheme, _, init_data = request.headers.get("Authorization", "").partition(" ")
        now = datetime.now(timezone.utc)
        status, payload = 401, {"error": "invalid_authorization"}
        if scheme.lower() == "tma" and init_data:
            try:
                authenticated = safe_parse_webapp_init_data(token, init_data)
                age = now - authenticated.auth_date
            except (ValueError, TypeError, OverflowError):
                pass
            else:
                if authenticated.user and -INIT_DATA_CLOCK_SKEW <= age <= INIT_DATA_MAX_AGE:
                    status, payload = await asyncio.to_thread(load, authenticated.user.id, now)
                    if status == 200:
                        payload["expires_in_ms"] = int(
                            (INIT_DATA_MAX_AGE - age).total_seconds() * 1000
                        )

        return web.json_response(payload, status=status, headers={"Cache-Control": "no-store"})

    app = web.Application()
    app.router.add_get("/api/diary", diary, allow_head=False)
    return app
