from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import hdrs, web
from aiohttp.helpers import content_disposition_header
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.access import can_access_bot
from food_registry_bot.db.models import Entry, EntryItem, User
from food_registry_bot.db.repositories import (
    DailyGoalSnapshotRepository,
    EntryRepository,
    GOAL_FIELDS,
    SUPPORTED_GOAL_METRIC_CODES,
    UserRepository,
    UserSummaryPreferenceRepository,
)
from food_registry_bot.diary_editing import (
    DIARY_MUTATION,
    DiaryEditError,
    DiaryMutation,
    apply_diary_mutation,
    dish_metric_values,
    dish_revision,
)
from food_registry_bot.mini_app_entries import (
    MINI_APP_ENTRY_COMMAND,
    CreateMiniAppEntry,
    MiniAppEntryError,
    QuickWaterEntry,
    RepeatMiniAppEntry,
    entry_user,
    write_prepared_entry,
)
from food_registry_bot.mini_app_files import (
    MAX_UPLOAD_BYTES,
    CsvDownload,
    CsvUpload,
    FileCommand,
    run_file_exchange,
)
from food_registry_bot.mini_app_settings import SettingsPatch, apply_settings, read_settings
from food_registry_bot.nutrition.beverage import beverage_volume, is_beverage, is_plain_water, nutrition_items
from food_registry_bot.nutrition.contract import SUPPORTED_NUTRITION_METRIC_CODES
from food_registry_bot.nutrition.daily_summary import (
    resolve_day_bounds_utc,
    resolve_local_summary_date,
)
from food_registry_bot.nutrition.period_report import (
    REPORT_COHORT_COUNTS,
    PeriodReportUseCase,
    resolve_report_cohorts,
)
from food_registry_bot.nutrition.workout_credit import DailyWorkoutCalorieCreditUseCase
from food_registry_bot.nutrition.water_summary import summarize_water_entries

MINI_APP_PORT = 8081
INIT_DATA_MAX_AGE = timedelta(hours=1)
INIT_DATA_CLOCK_SKEW = timedelta(seconds=30)


def metric_total(dishes: list[dict], code: str) -> dict:
    known = [dish["metrics"][code] for dish in dishes if dish["metrics"][code] is not None]
    return {
        "value": sum(known) if known else None,
        "complete": bool(known) and all(dish["water"] or dish["metrics"][code] is not None for dish in dishes),
    }


def diary_dish_data(item: EntryItem) -> dict:
    values = dish_metric_values(item)
    return {
        "id": item.id,
        "name": "Вода" if is_plain_water(item) else item.name,
        "quantity": item.quantity,
        "unit": item.unit,
        "revision": dish_revision(item, values),
        "calories": values.get("calories"),
        "metrics": {code: values.get(code) for code in SUPPORTED_NUTRITION_METRIC_CODES},
        "complete": all(code in values for code in SUPPORTED_NUTRITION_METRIC_CODES),
        "water": is_plain_water(item),
        "beverage": is_beverage(item),
    }


def diary_local_time(occurred_at: datetime, user_timezone: ZoneInfo) -> datetime:
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return occurred_at.astimezone(user_timezone)


def meal_data(entry: Entry, user_timezone: ZoneInfo) -> dict:
    dishes = [diary_dish_data(item) for item in sorted(entry.items, key=lambda item: item.position)]
    totals = {code: metric_total(dishes, code) for code in SUPPORTED_NUTRITION_METRIC_CODES}
    water_items = [item for item in entry.items if is_plain_water(item)]
    water_volumes = [beverage_volume(item) for item in water_items]
    totals["water"] = {
        "value": sum(water_volumes) if water_volumes and all(value is not None for value in water_volumes) else None,
        "complete": bool(water_volumes) and all(value is not None for value in water_volumes),
    }
    return {
        "id": entry.id,
        "time": diary_local_time(entry.occurred_at, user_timezone).strftime("%H:%M"),
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
        code for code in SUPPORTED_NUTRITION_METRIC_CODES
        if preference is None or getattr(preference, f"show_{code}")
    ]
    if preference is None or preference.show_water:
        metrics.append("water")
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
        entries = repository.list_diary_for_user_between(
            user_id=user.id, occurred_at_from=start, occurred_at_to=end
        )
        meals = [meal_data(entry, user_timezone) for entry in reversed(entries)]
        nutrition_ids = {item.id for entry in entries for item in nutrition_items(entry)}
        dishes = [dish for meal in meals for dish in meal["dishes"] if dish["id"] in nutrition_ids]
        water_summary = summarize_water_entries(entries, summary_date=day, timezone_name=user.timezone)
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
            total = (
                {"value": water_summary.total_ml if water_summary.included_entry_count else None,
                 "complete": water_summary.is_complete and bool(water_summary.included_entry_count)}
                if code == "water" else metric_total(dishes, code)
            )
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
                "complete": all(
                    dish["water"] or dish["metrics"][code] is not None
                    for dish in dishes for code in metrics if code != "water"
                ),
                "fluid_total_ml": water_summary.fluid_total_ml,
                "metrics": observations,
                "meals": meals,
            }
        )

    return {
        "metrics": metrics, "tolerance": tolerance, "days": days,
        "timezone": user.timezone, "day_start_hour": start_hour,
    }


def read_report(session: Session, user: User, now: datetime, group_count: int) -> dict:
    preference = UserSummaryPreferenceRepository(session).get_by_user_id(user.id)
    start_hour = preference.nutrition_day_start_hour if preference is not None else 4
    tolerance = preference.report_goal_tolerance_percent if preference is not None else 10
    codes = tuple(
        code for code in SUPPORTED_GOAL_METRIC_CODES
        if preference is None or getattr(preference, f"show_{code}")
    )
    today = resolve_local_summary_date(
        reference_at=now, timezone_name=user.timezone, nutrition_day_start_hour=start_hour
    )
    cohorts = resolve_report_cohorts(today, max(REPORT_COHORT_COUNTS))
    report = PeriodReportUseCase(session).run(
        user_id=user.id,
        timezone_name=user.timezone,
        summary_date_to=today,
        cohort_count=group_count,
        nutrition_day_start_hour=start_hour,
        workout_logging_enabled=user.workout_logging_enabled,
        report_goal_tolerance_percent=tolerance,
        dynamics_metric_codes=codes,
    )
    metrics = []
    for code in codes:
        data_days = report.water_data_day_count if code == "water" else report.food_data_day_count
        average = report.average_water_ml if code == "water" else getattr(report.average_nutrition_totals, code)
        metrics.append({
            "code": code,
            "average": average if data_days else None,
            "data_days": data_days,
            "incomplete_days": report.incomplete_water_day_count if code == "water" else report.incomplete_food_day_count,
            "goal_hits": report.goal_hit_day_counts[code],
            "goal_days": report.goal_applicable_day_counts[code],
            "groups": [row.model_dump(mode="json") for row in report.metric_dynamics[code]],
        })
    return {
        "periods": [
            {"groups": count, "days": (today - cohorts[-count][0]).days + 1}
            for count in REPORT_COHORT_COUNTS
        ],
        "date_from": report.summary_date_from.isoformat(),
        "date_to": report.summary_date_to.isoformat(),
        "days": report.period_day_count,
        "fluid_total_ml": report.fluid_total_ml if "water" in codes else None,
        "tolerance": tolerance,
        "metrics": metrics,
    }


def create_diary_app(
    session_factory: sessionmaker[Session], *, token: str, admin_user_ids: tuple[int, ...],
    write_entry: Callable[[int, tuple[int, ...], CreateMiniAppEntry], date] | None = None,
) -> web.Application:
    def load(
        telegram_user_id: int, now: datetime, path: str,
        command: DiaryMutation | SettingsPatch | None, group_count: int | None,
    ) -> tuple[int, dict]:
        try:
            with session_factory.begin() as session:
                if not can_access_bot(session, telegram_user_id, admin_user_ids):
                    return 403, {"error": "access_denied"}
                user = UserRepository(session).get_by_telegram_user_id(telegram_user_id)
                if user is None:
                    return 404, {"error": "profile_missing"}
                if path == "/api/settings":
                    if isinstance(command, SettingsPatch) and not apply_settings(session, user, command):
                        return 422, {"error": "unknown_diet"}
                    return 200, read_settings(session, user)
                if group_count is not None:
                    return 200, read_report(session, user, now, group_count)
                if command is not None:
                    apply_diary_mutation(session, user, command)
                    session.flush()
                    session.expire_all()
                return 200, read_diary(session, user, now)
        except DiaryEditError as exc:
            return exc.status, {"error": exc.code}

    def response(status: int, payload: dict) -> web.Response:
        return web.json_response(payload, status=status, headers={"Cache-Control": "no-store"})

    def load_history(telegram_user_id: int, query: str) -> dict:
        with session_factory.begin() as session:
            user = entry_user(session, telegram_user_id, admin_user_ids)
            user_timezone = ZoneInfo(user.timezone)
            results = EntryRepository(session).list_food_items_for_user(
                user_id=user.id, name_query=query, limit=25,
            )
            return {"items": [
                {
                    **diary_dish_data(item), "entry_id": item.entry_id,
                    "occurred_at": diary_local_time(occurred_at, user_timezone).isoformat(),
                }
                for item, occurred_at in results
            ]}

    async def create_entry(request: web.Request, telegram_user_id: int) -> web.Response:
        if request.method == "GET":
            query = request.query.get("q", "").strip()
            if len(query) > 100:
                return response(400, {"error": "invalid_history_query"})
            try:
                return response(200, await asyncio.to_thread(load_history, telegram_user_id, query))
            except MiniAppEntryError as exc:
                return response(exc.status, {"error": exc.code})
        invalid = {"error": "invalid_entry"}
        if request.content_type != "application/json":
            return response(415, invalid)
        try:
            command = MINI_APP_ENTRY_COMMAND.validate_json(await request.read())
        except ValidationError:
            return response(400, invalid)
        try:
            if isinstance(command, (RepeatMiniAppEntry, QuickWaterEntry)):
                summary_date = await asyncio.to_thread(
                    write_prepared_entry, session_factory, telegram_user_id, admin_user_ids, command,
                )
            elif write_entry is not None:
                summary_date = await asyncio.to_thread(write_entry, telegram_user_id, admin_user_ids, command)
            else:
                return response(503, {"error": "entry_provider_unavailable"})
        except MiniAppEntryError as exc:
            return response(exc.status, {"error": exc.code})
        return response(200, {"summary_date": summary_date.isoformat()})

    async def exchange_files(request: web.Request, telegram_user_id: int) -> web.Response:
        command: FileCommand | CsvUpload | None = None
        if request.method == "POST":
            if request.content_type == "application/octet-stream":
                filename = request.query.get("name", "")
                if (not filename.lower().endswith(".csv") or len(filename) > 255
                        or any(char in "/\\" or ord(char) < 32 for char in filename)):
                    return response(400, {"error": "invalid_filename"})
                content = bytearray()
                async for chunk in request.content.iter_chunked(64 * 1024):
                    if len(content) + len(chunk) > MAX_UPLOAD_BYTES:
                        return response(413, {"error": "file_too_large"})
                    content.extend(chunk)
                command = CsvUpload(filename, bytes(content))
            elif request.content_type == "application/json":
                try:
                    command = FileCommand.model_validate_json(await request.read())
                except ValidationError:
                    return response(400, {"error": "invalid_file_action"})
            else:
                return response(415, {"error": "invalid_file_action"})
        result = await asyncio.to_thread(
            run_file_exchange, session_factory, telegram_user_id, admin_user_ids, command,
        )
        if isinstance(result, CsvDownload):
            return web.Response(body=result.content, content_type="text/csv", headers={
                "Cache-Control": "no-store",
                hdrs.CONTENT_DISPOSITION: content_disposition_header("attachment", filename=result.filename),
            })
        return response(*result)

    async def mini_app_request(request: web.Request) -> web.Response:
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
                    if request.path == "/api/files":
                        return await exchange_files(request, authenticated.user.id)
                    if request.path == "/api/entries":
                        return await create_entry(request, authenticated.user.id)
                    command = None
                    group_count = None
                    if request.path == "/api/report":
                        groups = request.query.get("groups", str(REPORT_COHORT_COUNTS[0]))
                        if groups not in {str(count) for count in REPORT_COHORT_COUNTS}:
                            return response(400, {"error": "invalid_group_count"})
                        group_count = int(groups)
                    elif request.method == "POST":
                        is_settings = request.path == "/api/settings"
                        error = {"error": "invalid_settings" if is_settings else "invalid_edit"}
                        if request.content_type != "application/json":
                            return response(415, error)
                        try:
                            body = await request.read()
                            command = (
                                SettingsPatch.model_validate_json(body)
                                if is_settings else DIARY_MUTATION.validate_json(body)
                            )
                        except ValidationError:
                            return response(400, error)
                    status, payload = await asyncio.to_thread(
                        load, authenticated.user.id, now, request.path, command, group_count
                    )
                    if status == 200:
                        payload["expires_in_ms"] = int(
                            (INIT_DATA_MAX_AGE - age).total_seconds() * 1000
                        )

        return response(status, payload)

    app = web.Application(client_max_size=4096)
    app.router.add_get("/api/diary", mini_app_request, allow_head=False)
    app.router.add_post("/api/diary", mini_app_request)
    app.router.add_get("/api/report", mini_app_request, allow_head=False)
    app.router.add_get("/api/settings", mini_app_request, allow_head=False)
    app.router.add_post("/api/settings", mini_app_request)
    app.router.add_get("/api/entries", mini_app_request, allow_head=False)
    app.router.add_post("/api/entries", mini_app_request)
    app.router.add_get("/api/files", mini_app_request, allow_head=False)
    app.router.add_post("/api/files", mini_app_request)
    return app
