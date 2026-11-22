from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.access import can_access_bot
from food_registry_bot.db.models import Entry, User
from food_registry_bot.db.repositories import (
    DailyGoalSnapshotRepository,
    EntryRepository,
    UserRepository,
    UserSummaryPreferenceRepository,
)
from food_registry_bot.diary_editing import (
    DIARY_METRICS,
    DIARY_MUTATION,
    DiaryEditError,
    DiaryMutation,
    apply_diary_mutation,
    dish_metric_values,
    dish_revision,
)
from food_registry_bot.nutrition.contract import SUPPORTED_NUTRITION_METRIC_CODES
from food_registry_bot.nutrition.daily_summary import (
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


def metric_total(dishes: list[dict], code: str) -> dict:
    known = [dish["metrics"][code] for dish in dishes if dish["metrics"][code] is not None]
    return {
        "value": sum(known) if known else None,
        "complete": bool(known) and len(known) == len(dishes),
    }


def meal_data(entry: Entry, user_timezone: ZoneInfo) -> dict:
    dishes = []
    for item in sorted(entry.items, key=lambda item: item.position):
        values = dish_metric_values(item)
        dishes.append(
            {
                "id": item.id,
                "name": item.name,
                "quantity": item.quantity,
                "unit": item.unit,
                "revision": dish_revision(item, values),
                "calories": values.get("calories"),
                "metrics": {code: values.get(code) for code in DIARY_METRICS},
                "complete": all(code in values for code in SUPPORTED_NUTRITION_METRIC_CODES),
            }
        )
    totals = {code: metric_total(dishes, code) for code in DIARY_METRICS}
    occurred_at = entry.occurred_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return {
        "id": entry.id,
        "time": occurred_at.astimezone(user_timezone).strftime("%H:%M"),
        "dishes": dishes,
        "totals": totals,
        "calories": totals["calories"]["value"],
        "complete": totals["calories"]["complete"],
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
        meals = [meal_data(entry, user_timezone) for entry in reversed(entries)]
        dishes = [dish for meal in meals for dish in meal["dishes"]]
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
            total = metric_total(dishes, code)
            value = total["value"]
            goal = goals[code] or None
            observations[code] = {
                "value": value,
                "goal": goal,
                "percent": value / goal * 100 if value is not None and goal else None,
                "lower": goal * (1 - tolerance / 100) if goal else None,
                "upper": goal * (1 + tolerance / 100) if goal else None,
                "complete": total["complete"],
            }

        days.append(
            {
                "date": day.isoformat(),
                "current": day == today,
                "complete": all(meal["dishes"] for meal in meals)
                and all(dish["complete"] for dish in dishes),
                "metrics": observations,
                "meals": meals,
            }
        )

    return {"metrics": metrics, "tolerance": tolerance, "days": days}


def create_diary_app(
    session_factory: sessionmaker[Session], *, token: str, admin_user_ids: tuple[int, ...]
) -> web.Application:
    def load(
        telegram_user_id: int, now: datetime, command: DiaryMutation | None
    ) -> tuple[int, dict]:
        try:
            with session_factory.begin() as session:
                if not can_access_bot(session, telegram_user_id, admin_user_ids):
                    return 403, {"error": "access_denied"}
                user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
                if user is None:
                    return 404, {"error": "profile_missing"}
                if command is not None:
                    apply_diary_mutation(session, user, command)
                    session.flush()
                    session.expire_all()
                return 200, read_diary(session, user, now)
        except DiaryEditError as exc:
            return exc.status, {"error": exc.code}

    def response(status: int, payload: dict) -> web.Response:
        return web.json_response(payload, status=status, headers={"Cache-Control": "no-store"})

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
                    command = None
                    if request.method == "POST":
                        if request.content_type != "application/json":
                            return response(415, {"error": "invalid_edit"})
                        try:
                            command = DIARY_MUTATION.validate_json(await request.read())
                        except ValidationError:
                            return response(400, {"error": "invalid_edit"})
                    status, payload = await asyncio.to_thread(
                        load, authenticated.user.id, now, command
                    )
                    if status == 200:
                        payload["expires_in_ms"] = int(
                            (INIT_DATA_MAX_AGE - age).total_seconds() * 1000
                        )

        return response(status, payload)

    app = web.Application(client_max_size=4096)
    app.router.add_get("/api/diary", diary, allow_head=False)
    app.router.add_post("/api/diary", diary)
    return app
