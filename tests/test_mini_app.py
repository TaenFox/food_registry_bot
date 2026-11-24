from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, time, timedelta, timezone
from threading import Barrier
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from aiohttp.test_utils import make_mocked_request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from food_registry_bot.config import Settings
from food_registry_bot.conversation import DisabledConversationService
from food_registry_bot.db.base import Base
from food_registry_bot.db.models import (
    DailyGoalSnapshot,
    Entry,
    EntryItem,
    EntryItemMetric,
    EntryType,
    MiniAppEntrySubmission,
    SupportedDiet,
    SupportedMetric,
    User,
    UserAccess,
    UserGoalPreference,
    UserSummaryPreference,
)
from food_registry_bot.extraction import ExtractedJournalPayload, ValidExtractionPayload
from food_registry_bot.mini_app import create_diary_app
from food_registry_bot.mini_app_entries import MiniAppEntryWriter
from food_registry_bot.nutrition import (
    BackfillNutritionEstimationUseCase,
    StaticNutritionEstimationService,
)
from food_registry_bot.nutrition.journal_adapter import (
    prepare_nutrition_request_from_entries,
    recoverable_food_weights,
)

TOKEN = "12345:test-token"


@pytest.fixture
def diary_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'diary.sqlite3'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory.begin() as session:
        session.add_all(
            SupportedMetric(code=code, name=code, unit="kcal" if code == "calories" else "g")
            for code in ("calories", "protein", "fat", "carbs", "fiber")
        )
    yield factory
    engine.dispose()


def telegram_init_data(user_id, *, age=timedelta(0), now=None):
    now = now or datetime.now(timezone.utc)
    fields = {
        "auth_date": str(int((now - age).timestamp())),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    check_string = "\n".join(f"{name}={value}" for name, value in sorted(fields.items()))
    secret = hmac.digest(b"WebAppData", TOKEN.encode(), "sha256")
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


async def request_diary(app, *, init_data=None, query="", method="GET", command=None, path="/api/diary"):
    headers = {"Authorization": f"tma {init_data}"} if init_data is not None else {}
    if method == "POST":
        headers["Content-Type"] = "application/json"
    request = make_mocked_request(method, path + query, headers=headers, app=app)
    read_body = AsyncMock(return_value=json.dumps(command).encode())
    request.read = read_body
    route = await app.router.resolve(request)
    response = await route.handler(request)
    if method == "POST" and response.status == 401:
        read_body.assert_not_awaited()
    assert response.headers["Cache-Control"] == "no-store"
    return response.status, json.loads(response.body)


@pytest.mark.parametrize("case", ["missing", "forged", "old", "future", "malformed"])
@pytest.mark.parametrize("path", ["/api/diary", "/api/report", "/api/settings"])
async def test_mini_app_rejects_untrusted_init_data_before_reading_db(case, path):
    def inaccessible_db():
        pytest.fail("Authorization must be checked before accessing the database")

    app = create_diary_app(inaccessible_db, token=TOKEN, admin_user_ids=())
    init_data = {
        "missing": None,
        "forged": telegram_init_data(1).replace("%3A+1%2C", "%3A+2%2C"),
        "old": telegram_init_data(1, age=timedelta(hours=2)),
        "future": telegram_init_data(1, age=-timedelta(minutes=2)),
        "malformed": "hash=★",
    }[case]

    assert await request_diary(app, init_data=init_data, path=path) == (
        401,
        {"error": "invalid_authorization"},
    )


@pytest.mark.parametrize("path", ["/api/diary", "/api/report", "/api/settings"])
async def test_mini_app_requires_bot_access_even_for_signed_users(diary_db, path):
    factory = diary_db
    with factory.begin() as session:
        session.add(User(telegram_user_id=1))
        session.add(UserAccess(telegram_user_id=2, is_allowed=True))
    app = create_diary_app(factory, token=TOKEN, admin_user_ids=())

    assert await request_diary(app, init_data=telegram_init_data(1), path=path) == (
        403,
        {"error": "access_denied"},
    )
    assert await request_diary(app, init_data=telegram_init_data(2), path=path) == (
        404,
        {"error": "profile_missing"},
    )


async def test_report_uses_annual_groups_and_saved_goals(diary_db, monkeypatch):
    now = datetime(2026, 1, 3, 5, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr("food_registry_bot.mini_app.datetime", FrozenDateTime)
    with diary_db.begin() as session:
        owner = User(telegram_user_id=1, timezone="Europe/Moscow")
        other = User(telegram_user_id=2, timezone="Europe/Moscow")
        session.add_all([owner, other, UserAccess(telegram_user_id=1, is_allowed=True)])
        session.flush()
        session.add(UserSummaryPreference(user_id=owner.id, nutrition_day_start_hour=6,
                                          show_fat=False, report_goal_tolerance_percent=5))
        metrics = {metric.code: metric for metric in session.query(SupportedMetric).all()}
        for user, day, calories in ((owner, 1, 2100), (owner, 2, 2400), (owner, 3, None),
                                    (owner, 4, 6000), (other, 2, 12000)):
            session.add(Entry(
                user_id=user.id, entry_type=EntryType.FOOD,
                occurred_at=datetime(2026, 1, day, 3, tzinfo=timezone.utc),
                items=[EntryItem(name="Еда", position=0, metrics=[
                    EntryItemMetric(metric=metric, value=calories if code == "calories" else 10)
                    for code, metric in metrics.items() if calories is not None
                ])],
            ))
        session.add(Entry(
            user_id=owner.id, entry_type=EntryType.WATER,
            occurred_at=datetime(2026, 1, 2, 3, tzinfo=timezone.utc),
            items=[EntryItem(name="water", position=0, unit="ml", quantity=1000)],
        ))
        for day, goal in ((1, 2000), (2, 2400), (3, 2100)):
            session.add(DailyGoalSnapshot(
                user_id=owner.id, summary_date=now.date().replace(day=day), timezone="Europe/Moscow",
                nutrition_day_start_hour=6, calorie_goal=goal, protein_goal=120,
                fat_goal=70, carbs_goal=210, fiber_goal=30, water_goal=2000,
            ))
        user_id = owner.id

    app = create_diary_app(diary_db, token=TOKEN, admin_user_ids=())
    init_data = telegram_init_data(1, now=now)
    status, payload = await request_diary(app, path="/api/report", init_data=init_data, query="?user_id=2")
    assert status == 200
    assert payload["date_from"] == "2025-12-31"
    assert payload["date_to"] == "2026-01-03"
    assert payload["days"] == 4
    assert payload["tolerance"] == 5
    assert payload["periods"] == [{"groups": 2, "days": 4}, {"groups": 4, "days": 12}, {"groups": 8, "days": 28}]
    by_code = {metric["code"]: metric for metric in payload["metrics"]}
    assert list(by_code) == ["calories", "protein", "carbs", "fiber", "water"]
    calories = by_code["calories"]
    assert (calories["average"], calories["data_days"], calories["incomplete_days"]) == (1500, 3, 1)
    assert (calories["goal_hits"], calories["goal_days"]) == (2, 2)
    previous, current = calories["groups"]
    assert previous["summary_date_to"] == "2025-12-31"
    assert previous["average_value"] is None
    assert current == {
        "summary_date_from": "2026-01-01", "summary_date_to": "2026-01-04", "data_day_count": 3,
        "average_value": 1500, "average_goal_value": pytest.approx(6500 / 3),
        "min_goal_value": 2000, "max_goal_value": 2400,
    }
    assert by_code["water"]["average"] == 1000
    assert by_code["water"]["goal_days"] == 1

    status, refreshed = await request_diary(app, path="/api/report", init_data=init_data, query="?groups=4")
    assert status == 200
    assert refreshed["days"] == 12
    assert refreshed["metrics"][0]["groups"][-1] == current
    with diary_db() as session:
        snapshots = session.query(DailyGoalSnapshot).filter_by(user_id=user_id).all()
        assert len(snapshots) == 12
        assert max(snapshot.summary_date for snapshot in snapshots) == now.date()

    assert await request_diary(app, path="/api/report", init_data=init_data, query="?groups=10000") == (
        400, {"error": "invalid_group_count"},
    )


async def test_mini_app_settings_share_preferences_and_preserve_goal_snapshots(diary_db):
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    with diary_db.begin() as session:
        user = User(telegram_user_id=1, timezone="UTC")
        session.add_all([
            user, UserAccess(telegram_user_id=1, is_allowed=True),
            User(telegram_user_id=2, timezone="UTC"), UserAccess(telegram_user_id=2, is_allowed=True),
            SupportedDiet(code="gastritis", name="При гастрите", is_enabled=True),
        ])
        session.flush()
        session.add(DailyGoalSnapshot(
            user_id=user.id, summary_date=today, timezone="UTC", nutrition_day_start_hour=4,
            calorie_goal=1800, protein_goal=90, fat_goal=60, carbs_goal=210, fiber_goal=25, water_goal=2000,
        ))
        user_id = user.id
    app = create_diary_app(diary_db, token=TOKEN, admin_user_ids=())
    init_data = telegram_init_data(1)

    async def settings(command=None, *, authorization=init_data):
        return await request_diary(
            app, path="/api/settings", init_data=authorization,
            method="POST" if command is not None else "GET", command=command,
        )

    status, defaults = await settings()
    assert status == 200
    assert defaults["goals"]["protein"] == 90
    assert defaults["goal_steps"] == {
        "calories": 100, "protein": 5, "fat": 5, "carbs": 5, "fiber": 5, "water": 100,
    }
    assert defaults["diets"] == [{"code": "gastritis", "name": "При гастрите", "selected": False}]
    with diary_db() as session:
        assert session.query(UserGoalPreference).count() == 0
        assert session.query(UserSummaryPreference).count() == 0

    command = {
        "goals": {"protein": 95, "fiber": 30},
        "metrics": {"calories": False, "water": False},
        "workout_logging_enabled": True,
        "show_day_progress_bar": True,
        "show_post_entry_delta_suffix": False,
        "summary_display_mode": "bars",
        "nutrition_day_start_hour": 6,
        "report_goal_tolerance_percent": 15,
        "report_noticeable_entry_percentile": 85,
        "diets": {"gastritis": True},
    }
    assert await settings(command, authorization=None) == (401, {"error": "invalid_authorization"})
    assert await settings({**command, "goals": {"protein": "95"}}) == (400, {"error": "invalid_settings"})
    assert await settings({**command, "report_goal_tolerance_percent": 99}) == (
        400, {"error": "invalid_settings"},
    )
    assert await settings({**command, "diets": {"unknown": True}}) == (422, {"error": "unknown_diet"})
    assert (await settings())[1]["goals"] == defaults["goals"]

    status, updated = await settings(command)
    assert status == 200
    assert updated["goals"] == {**defaults["goals"], **command["goals"]}
    assert updated["metrics"] == {**defaults["metrics"], **command["metrics"]}
    assert updated["diets"] == [{**defaults["diets"][0], "selected": True}]
    assert all(updated[key] == value for key, value in command.items()
        if key not in {"goals", "metrics", "diets"})
    assert (await settings({"metrics": {"fat": False}}))[1]["goals"] == updated["goals"]
    assert (await settings(authorization=telegram_init_data(2)))[1]["goals"] == defaults["goals"]
    _, diary = await request_diary(app, init_data=init_data)
    assert diary["metrics"] == ["protein", "carbs", "fiber"]
    _, report = await request_diary(app, init_data=init_data, path="/api/report")
    assert [metric["code"] for metric in report["metrics"]] == ["protein", "carbs", "fiber"]

    with diary_db() as session:
        assert session.query(UserGoalPreference).filter_by(user_id=user_id).one().fiber_goal == 30
        assert session.query(UserSummaryPreference).filter_by(user_id=user_id).one().show_fat is False
        assert session.get(User, user_id).workout_logging_enabled
        snapshot = session.query(DailyGoalSnapshot).filter_by(summary_date=today).one()
        assert (snapshot.protein_goal, snapshot.fiber_goal, snapshot.nutrition_day_start_hour) == (90, 25, 4)


async def test_create_entry_sets_local_time_and_does_not_duplicate_a_retry(diary_db):
    _, app, extract, estimate = entry_writer(diary_db, [
        {"type": "food", "items": [{"name": "Гречка", "quantity": 150, "unit": "g"}]},
        {"type": "water", "items": [{"name": "water", "quantity": 250, "unit": "ml"}]},
    ])
    local_date = (datetime.now(timezone.utc) + timedelta(hours=4)).date()
    food_day = local_date - timedelta(days=1)
    command = {
        "request_id": str(uuid4()),
        "text": "Гречка 150 г и вода 250 мл",
        "local_date": local_date.isoformat(),
        "local_time": "02:15",
    }
    assert await submit_entry(app, command, user_id=None) == (401, {"error": "invalid_authorization"})
    assert await submit_entry(app, command, user_id=2) == (403, {"error": "access_denied"})
    assert await submit_entry(app, {**command, "text": "Как добрать белки?"}) == (
        422, {"error": "not_a_food_record"},
    )
    extract.assert_not_called()

    result = (200, {"summary_date": food_day.isoformat()})
    assert await submit_entry(app, command) == result
    assert await submit_entry(app, command) == result
    assert await submit_entry(app, {**command, "local_time": "02:30"}) == (
        409, {"error": "entry_request_conflict"},
    )
    assert extract.call_count == estimate.call_count == 1

    with diary_db() as session:
        food, water = session.query(Entry).order_by(Entry.id).all()
        assert (food.entry_type, water.entry_type) == (EntryType.FOOD, EntryType.WATER)
        assert food.occurred_at == water.occurred_at == datetime.combine(food_day, time(22, 15))
        assert food.source_text == command["text"]
        assert water.items[0].quantity == 250
        assert session.query(MiniAppEntrySubmission).count() == 1
        assert session.query(DailyGoalSnapshot).one().summary_date == food_day

    status, payload = await request_diary(app, init_data=telegram_init_data(1))
    assert status == 200
    assert payload["timezone"] == "Asia/Tbilisi"
    day = next(day for day in payload["days"] if day["date"] == food_day.isoformat())
    assert day["meals"][0]["time"] == "02:15"
    assert day["metrics"]["fiber"]["value"] == 4
    assert day["metrics"]["calories"]["goal"] == 1800

    with diary_db.begin() as session:
        for entry in session.query(Entry).all():
            session.delete(entry)
    assert await submit_entry(app, command) == result
    with diary_db() as session:
        assert session.query(Entry).count() == 0


async def test_create_entry_rolls_back_all_rows_and_allows_retry_after_failed_nutrition(diary_db):
    writer, app, _, estimate = entry_writer(diary_db, [
        {"type": "water", "items": [{"name": "water", "quantity": 250, "unit": "ml"}]},
        {"type": "food", "items": [{"name": "Рис", "quantity": 100, "unit": "g"}]},
    ])
    command = {
        "request_id": str(uuid4()), "text": "Рис и вода",
        "local_date": datetime.now(timezone.utc).date().isoformat(), "local_time": "13:15",
    }
    writer.nutrition_service = StaticNutritionEstimationService(raw_payload="")

    assert await submit_entry(app, command) == (422, {"error": "entry_nutrition_failed"})
    with diary_db() as session:
        assert session.query(Entry).count() == 0
        assert session.query(MiniAppEntrySubmission).count() == 0
        assert session.query(DailyGoalSnapshot).count() == 0

    writer.nutrition_service = Mock(estimate=estimate)
    assert (await submit_entry(app, command))[0] == 200


async def test_simultaneous_entry_retries_commit_only_once(diary_db):
    _, app, extract, estimate = entry_writer(diary_db, [
        {"type": "food", "items": [{"name": "Овсянка", "quantity": 180, "unit": "g"}]},
    ])
    both_requests = Barrier(2)

    def extract_together(_request):
        both_requests.wait(timeout=5)
        return extract.return_value

    extract.side_effect = extract_together
    command = {
        "request_id": str(uuid4()), "text": "Овсянка 180 г",
        "local_date": datetime.now(timezone.utc).date().isoformat(), "local_time": "13:15",
    }
    first, second = await asyncio.gather(submit_entry(app, command), submit_entry(app, command))
    assert first == second
    assert first[0] == 200
    assert estimate.call_count == 1
    with diary_db() as session:
        assert session.query(Entry).count() == 1
        assert session.query(MiniAppEntrySubmission).count() == 1


def entry_writer(factory, entries):
    with factory.begin() as session:
        session.add_all([
            User(telegram_user_id=1, timezone="Asia/Tbilisi"),
            User(telegram_user_id=2, timezone="UTC"),
            UserAccess(telegram_user_id=1, is_allowed=True),
        ])
    extracted = ValidExtractionPayload(
        payload=ExtractedJournalPayload.model_validate({"entries": entries}),
        extraction_provider="fixture", extraction_model=None, raw_payload="{}",
    )
    extract = Mock(return_value=extracted)

    def estimate_request(request):
        values = {"calories": 170, "protein": 5, "fat": 2, "carbs": 33, "fiber": 4}
        raw = json.dumps({"items": [
            {"client_item_id": item.client_item_id, "metrics": [
                {"code": code, "value": value, "confidence": "high"}
                for code, value in values.items()
            ]}
            for item in request.items
        ]})
        return StaticNutritionEstimationService(raw_payload=raw).estimate(request)

    estimate = Mock(side_effect=estimate_request)
    writer = MiniAppEntryWriter(
        factory,
        Settings(_env_file=None, EXTRACTION_PROVIDER="structured_payload",
                 NUTRITION_PROVIDER="static", ENABLE_OPENAI_PROVIDER=False),
        Mock(extract=extract), Mock(estimate=estimate), DisabledConversationService(),
    )
    app = create_diary_app(factory, token=TOKEN, admin_user_ids=(), write_entry=writer.write)
    return writer, app, extract, estimate


async def submit_entry(app, command, *, user_id=1):
    return await request_diary(
        app, path="/api/entries", method="POST", command=command,
        init_data=telegram_init_data(user_id) if user_id is not None else None,
    )


async def test_diary_preserves_daily_goals_and_meal_groups_with_partial_data(diary_db):
    factory = diary_db
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    today = (now - timedelta(hours=4)).date()
    yesterday = today - timedelta(days=1)
    older = today - timedelta(days=2)
    with factory.begin() as session:
        owner = User(telegram_user_id=1, timezone="UTC")
        other = User(telegram_user_id=2, timezone="UTC")
        session.add_all([owner, other, UserAccess(telegram_user_id=1, is_allowed=True)])
        session.flush()
        session.add(UserSummaryPreference(user_id=owner.id, show_fat=False))
        metrics = {metric.code: metric for metric in session.query(SupportedMetric).all()}

        def meal(user, at, dishes):
            session.add(
                Entry(
                    user_id=user.id,
                    entry_type=EntryType.FOOD,
                    occurred_at=at,
                    source_text="Приватный исходный текст сообщения",
                    items=[
                        EntryItem(
                            name=name,
                            position=index,
                            metrics=[
                                EntryItemMetric(metric=metrics[code], value=value)
                                for code, value in values.items()
                            ],
                        )
                        for index, (name, values) in enumerate(dishes)
                    ],
                )
            )

        half_meal = {"calories": 500, "protein": 25, "fat": 20, "carbs": 55, "fiber": 6}
        meal(owner, now, [("Рис", half_meal), ("Тунец", half_meal)])
        meal(owner, now - timedelta(days=1), [("Курица", {**half_meal, "calories": 1000})])
        meal(owner, now - timedelta(days=2), [("Импорт", {"calories": 240}), ("Суп", {})])
        meal(other, now, [("Чужая запись", half_meal)])

        for day, goal in ((today, 2000), (yesterday, 2500)):
            session.add(
                DailyGoalSnapshot(
                    user_id=owner.id,
                    summary_date=day,
                    timezone="UTC",
                    nutrition_day_start_hour=4,
                    calorie_goal=goal,
                    protein_goal=120,
                    fat_goal=70,
                    carbs_goal=210,
                    fiber_goal=30,
                    water_goal=2000,
                )
            )
    app = create_diary_app(factory, token=TOKEN, admin_user_ids=())

    status, payload = await request_diary(app, init_data=telegram_init_data(1), query="?user_id=2")

    assert status == 200
    assert 0 < payload["expires_in_ms"] <= 3_600_000
    assert payload["metrics"] == ["calories", "protein", "carbs", "fiber"]
    assert len(payload["days"]) == 5
    assert "Чужая запись" not in str(payload)
    assert "Приватный исходный текст сообщения" not in str(payload)
    by_date = {day["date"]: day for day in payload["days"]}
    today_data = by_date[today.isoformat()]
    assert today_data["current"]
    assert today_data["metrics"]["calories"] == {
        "value": 1000,
        "goal": 2000,
        "percent": 50,
        "lower": 1800,
        "upper": 2200,
        "complete": True,
    }
    assert by_date[yesterday.isoformat()]["metrics"]["calories"]["percent"] == 40
    assert today_data["metrics"]["fiber"] == {
        "value": 12, "goal": 30, "percent": 40,
        "lower": 27, "upper": pytest.approx(33), "complete": True,
    }
    (meal,) = today_data["meals"]
    assert meal["time"] == now.strftime("%H:%M")
    assert meal["complete"]
    assert meal["calories"] == 1000
    assert meal["totals"]["protein"] == {"value": 50, "complete": True}
    assert meal["totals"]["fiber"] == {"value": 12, "complete": True}
    assert meal["dishes"][0]["metrics"]["fiber"] == 6
    assert [(dish["name"], dish["calories"]) for dish in meal["dishes"]] == [
        ("Рис", 500),
        ("Тунец", 500),
    ]
    imported = by_date[older.isoformat()]
    assert imported["metrics"]["calories"]["percent"] is None
    assert imported["metrics"]["calories"]["value"] == 240
    assert not imported["complete"]
    assert not imported["meals"][0]["complete"]
    assert imported["meals"][0]["dishes"][1]["calories"] is None
    with factory() as session:
        assert session.query(DailyGoalSnapshot).count() == 2


@pytest.fixture
def editable_diary(diary_db):
    today = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    day = today - timedelta(days=1)
    with diary_db.begin() as session:
        owner = User(telegram_user_id=1, timezone="UTC")
        other = User(telegram_user_id=2, timezone="UTC")
        session.add_all([owner, other, UserAccess(telegram_user_id=1, is_allowed=True)])
        session.flush()
        metrics = {metric.code: metric for metric in session.query(SupportedMetric).all()}

        def meal(user, at, name, *, quantity=None, complete=True, entry_type=EntryType.FOOD):
            values = {"calories": 200, "protein": 10, "fat": 5, "carbs": 20, "fiber": 4}
            if not complete:
                values.pop("fiber")
            item = EntryItem(
                name=name,
                position=0,
                quantity=quantity,
                unit="g" if quantity is not None else None,
                metrics=[
                    EntryItemMetric(metric=metrics[code], value=value)
                    for code, value in values.items()
                ],
            )
            entry = Entry(user_id=user.id, entry_type=entry_type, occurred_at=at, items=[item])
            session.add(entry)
            session.flush()
            return entry, item

        late = datetime.combine(day, time(23), tzinfo=timezone.utc)
        early = datetime.combine(today, time(1), tzinfo=timezone.utc)
        source, dish = meal(owner, late, "рис", complete=False)
        target, target_dish = meal(owner, early, "курица", quantity=80)
        target_dish.position = 5
        foreign, _ = meal(other, early, "чужое блюдо", quantity=200)
        previous, _ = meal(owner, late - timedelta(days=1), "груша")
        water, _ = meal(owner, early, "вода", quantity=250, entry_type=EntryType.WATER)
        source.source_text = "Запиши 120 г риса"
        source.extraction_raw_payload = json.dumps(
            {
                "entries": [
                    {
                        "type": "food",
                        "items": [
                            {
                                "name": "рис",
                                "nutrition_label": {
                                    "basis": "per_100g",
                                    "calories": 300,
                                    "protein": 10,
                                    "fat": 4,
                                    "carbs": 60,
                                },
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )
        ids = {
            "source": source.id,
            "dish": dish.id,
            "target": target.id,
            "target_dish": target_dish.id,
            "foreign": foreign.id,
            "previous": previous.id,
            "water": water.id,
        }
    return diary_db, ids


async def mutate_diary(app, command, *, user_id=1):
    return await request_diary(
        app, init_data=telegram_init_data(user_id), method="POST", command=command
    )


def find_meal(payload, entry_id):
    return next(meal for day in payload["days"] for meal in day["meals"] if meal["id"] == entry_id)


async def test_move_last_dish_checks_confirmation_and_preserves_facts(editable_diary):
    factory, ids = editable_diary
    app = create_diary_app(factory, token=TOKEN, admin_user_ids=())
    command = {
        "action": "move",
        "entry_id": ids["source"],
        "item_id": ids["dish"],
        "target_entry_id": ids["target"],
    }
    _, before = await request_diary(app, init_data=telegram_init_data(1))

    assert await request_diary(app, method="POST", command=command) == (
        401,
        {"error": "invalid_authorization"},
    )
    assert await mutate_diary(app, command) == (409, {"error": "confirm_empty_entry"})
    assert await mutate_diary(app, {**command, "target_entry_id": ids["foreign"]}) == (
        404,
        {"error": "entry_missing"},
    )
    assert await mutate_diary(app, {**command, "target_entry_id": ids["water"]}) == (
        404,
        {"error": "entry_missing"},
    )
    assert await mutate_diary(app, {**command, "target_entry_id": ids["previous"]}) == (
        400,
        {"error": "different_food_day"},
    )
    status, after = await mutate_diary(app, {**command, "confirm_delete": True})

    assert status == 200
    assert [day["metrics"] for day in before["days"]] == [day["metrics"] for day in after["days"]]
    assert [dish["id"] for dish in find_meal(after, ids["target"])["dishes"]] == [
        ids["target_dish"],
        ids["dish"],
    ]
    with factory() as session:
        assert session.get(Entry, ids["source"]) is None
        dish = session.get(EntryItem, ids["dish"])
        assert dish.position == 6
        assert len(dish.metrics) == 4
        target = session.get(Entry, ids["target"])
        assert recoverable_food_weights(target) == {dish.id: 120}
        prepared = prepare_nutrition_request_from_entries([target])
        assert prepared.request.items[-1].nutrition_label.calories == 300
    assert await mutate_diary(app, {**command, "confirm_delete": True}) == (
        404,
        {"error": "entry_missing"},
    )


async def test_edit_unknown_weight_requires_four_values_and_rejects_stale_edit(editable_diary):
    factory, ids = editable_diary
    app = create_diary_app(factory, token=TOKEN, admin_user_ids=())
    _, before = await request_diary(app, init_data=telegram_init_data(1))
    (dish,) = find_meal(before, ids["source"])["dishes"]
    base = {
        "action": "edit",
        "entry_id": ids["source"],
        "item_id": dish["id"],
        "revision": dish["revision"],
        "weight": 150,
    }
    assert await mutate_diary(app, base) == (422, {"error": "missing_per_100g"})
    assert await mutate_diary(app, {**base, "weight": 0}) == (400, {"error": "invalid_edit"})
    assert await mutate_diary(app, base, user_id=2) == (403, {"error": "access_denied"})
    command = {**base, "per_100g": {"calories": 180, "protein": 5, "fat": 2, "carbs": 31}}
    status, after = await mutate_diary(app, command)

    assert status == 200
    (updated,) = find_meal(after, ids["source"])["dishes"]
    assert (updated["quantity"], updated["unit"]) == (150, "g")
    assert updated["metrics"] == {"calories": 270, "protein": 7.5, "fat": 3, "carbs": 46.5, "fiber": None}
    assert await mutate_diary(app, command) == (409, {"error": "dish_changed"})
    status, scaled = await mutate_diary(
        app, {**base, "revision": updated["revision"], "weight": 200}
    )
    assert status == 200
    (updated,) = find_meal(scaled, ids["source"])["dishes"]
    assert updated["metrics"] == {"calories": 360, "protein": 10, "fat": 4, "carbs": 62, "fiber": None}
    with factory() as session:
        item = session.get(EntryItem, ids["dish"])
        assert item.source_facts["manual_metrics"] == ["calories", "carbs", "fat", "protein"]
        assert item.source_facts["nutrition_label"]["calories"] == 300
        raw = json.dumps(
            {
                "items": [
                    {
                        "client_item_id": f"entry-{ids['source']}:item-0",
                        "metrics": [
                            {"code": code, "value": 7, "confidence": "medium"}
                            for code in ("calories", "protein", "fat", "carbs", "fiber")
                        ],
                    }
                ]
            }
        )
        backfill = BackfillNutritionEstimationUseCase(
            session, StaticNutritionEstimationService(raw_payload=raw)
        )
        assert backfill.run(limit=1).saved_metric_count == 1
        session.expire(item, ["metrics"])
        assert {metric.metric.code: metric.value for metric in item.metrics} == {
            **updated["metrics"],
            "fiber": 7,
        }


async def test_edit_known_weight_scales_existing_fiber(editable_diary):
    factory, ids = editable_diary
    app = create_diary_app(factory, token=TOKEN, admin_user_ids=())
    _, before = await request_diary(app, init_data=telegram_init_data(1))
    (dish,) = find_meal(before, ids["target"])["dishes"]
    status, after = await mutate_diary(
        app,
        {
            "action": "edit",
            "entry_id": ids["target"],
            "item_id": dish["id"],
            "revision": dish["revision"],
            "weight": 120,
            "per_100g": {"protein": 40},
        },
    )

    assert status == 200
    (updated,) = find_meal(after, ids["target"])["dishes"]
    assert updated["metrics"] == {"calories": 300, "protein": 48, "fat": 7.5, "carbs": 30, "fiber": 6}
    with factory() as session:
        item = session.get(EntryItem, ids["target_dish"])
        assert next(metric.value for metric in item.metrics if metric.metric.code == "fiber") == 6


async def test_edit_drink_preserves_saved_volume_and_scales_metrics(editable_diary):
    factory, ids = editable_diary
    with factory.begin() as session:
        item = session.get(EntryItem, ids["target_dish"])
        item.name = "кефир"
        item.quantity = 250
        item.unit = "мл"

    app = create_diary_app(factory, token=TOKEN, admin_user_ids=())
    _, before = await request_diary(app, init_data=telegram_init_data(1))
    (dish,) = find_meal(before, ids["target"])["dishes"]
    assert (dish["quantity"], dish["unit"]) == (250, "мл")
    command = {
        "action": "edit",
        "entry_id": ids["target"],
        "item_id": dish["id"],
        "revision": dish["revision"],
        "quantity": 500,
        "unit": "ml",
        "per_100": {"calories": 35},
    }
    assert await mutate_diary(app, {**command, "unit": "g"}) == (422, {"error": "missing_per_100g"})
    status, after = await mutate_diary(app, command)

    assert status == 200
    (updated,) = find_meal(after, ids["target"])["dishes"]
    assert (updated["quantity"], updated["unit"]) == (500, "ml")
    assert updated["metrics"] == {"calories": 175, "protein": 20, "fat": 10, "carbs": 40, "fiber": 8}
    with factory() as session:
        item = session.get(EntryItem, dish["id"])
        assert next(metric.value for metric in item.metrics if metric.metric.code == "fiber") == 8

    status, converted = await mutate_diary(
        app,
        {
            **command,
            "revision": updated["revision"],
            "quantity": 100,
            "unit": "g",
            "per_100": {"calories": 50, "protein": 2, "fat": 1, "carbs": 8},
        },
    )
    assert status == 200
    (updated,) = find_meal(converted, ids["target"])["dishes"]
    assert (updated["quantity"], updated["unit"]) == (100, "g")
    assert updated["metrics"] == {"calories": 50, "protein": 2, "fat": 1, "carbs": 8, "fiber": 8}


async def test_delete_dish_removes_metrics_and_requires_confirmation_for_last_dish(editable_diary):
    factory, ids = editable_diary
    with factory.begin() as session:
        session.add(EntryItem(entry_id=ids["target"], position=6, name="овощи"))
    app = create_diary_app(factory, token=TOKEN, admin_user_ids=())
    _, before = await request_diary(app, init_data=telegram_init_data(1))
    dish, remaining = find_meal(before, ids["target"])["dishes"]
    command = {
        "action": "delete",
        "entry_id": ids["target"],
        "item_id": dish["id"],
        "revision": dish["revision"],
    }
    assert await mutate_diary(app, {**command, "entry_id": ids["foreign"]}) == (
        404,
        {"error": "entry_missing"},
    )
    assert await mutate_diary(app, {**command, "revision": "0" * 32}) == (409, {"error": "dish_changed"})
    status, after = await mutate_diary(app, command)

    assert status == 200
    assert find_meal(after, ids["target"])["dishes"] == [remaining]
    assert find_meal(after, ids["target"])["totals"]["calories"]["value"] is None
    with factory() as session:
        assert session.get(EntryItem, dish["id"]) is None
        assert not session.query(EntryItemMetric).filter_by(entry_item_id=dish["id"]).count()

    last_dish = {**command, "item_id": remaining["id"], "revision": remaining["revision"]}
    assert await mutate_diary(app, last_dish) == (409, {"error": "confirm_empty_entry"})
    status, after = await mutate_diary(app, {**last_dish, "confirm_delete": True})
    assert status == 200
    assert all(meal["id"] != ids["target"] for day in after["days"] for meal in day["meals"])
    with factory() as session:
        assert session.get(Entry, ids["target"]) is None
        assert session.get(EntryItem, remaining["id"]) is None


async def test_move_from_multiple_dishes_keeps_the_source_entry(editable_diary):
    factory, ids = editable_diary
    with factory.begin() as session:
        session.add(EntryItem(entry_id=ids["source"], position=1, name="овощи"))
    app = create_diary_app(factory, token=TOKEN, admin_user_ids=())
    status, payload = await mutate_diary(
        app,
        {
            "action": "move",
            "entry_id": ids["source"],
            "item_id": ids["dish"],
            "target_entry_id": ids["target"],
        },
    )

    assert status == 200
    assert [dish["name"] for dish in find_meal(payload, ids["source"])["dishes"]] == ["овощи"]
    assert [dish["id"] for dish in find_meal(payload, ids["target"])["dishes"]] == [
        ids["target_dish"],
        ids["dish"],
    ]
