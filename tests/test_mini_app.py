from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, time, timedelta, timezone
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import make_mocked_request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import (
    DailyGoalSnapshot,
    Entry,
    EntryItem,
    EntryItemMetric,
    EntryType,
    SupportedMetric,
    User,
    UserAccess,
    UserSummaryPreference,
)
from food_registry_bot.mini_app import create_diary_app
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


def telegram_init_data(user_id, *, age=timedelta(0)):
    fields = {
        "auth_date": str(int((datetime.now(timezone.utc) - age).timestamp())),
        "user": json.dumps({"id": user_id, "first_name": "Test"}),
    }
    check_string = "\n".join(f"{name}={value}" for name, value in sorted(fields.items()))
    secret = hmac.digest(b"WebAppData", TOKEN.encode(), "sha256")
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


async def request_diary(app, *, init_data=None, query="", method="GET", command=None):
    headers = {"Authorization": f"tma {init_data}"} if init_data is not None else {}
    if method == "POST":
        headers["Content-Type"] = "application/json"
    request = make_mocked_request(method, "/api/diary" + query, headers=headers, app=app)
    read_body = AsyncMock(return_value=json.dumps(command).encode())
    request.read = read_body
    route = await app.router.resolve(request)
    response = await route.handler(request)
    if method == "POST" and response.status == 401:
        read_body.assert_not_awaited()
    assert response.headers["Cache-Control"] == "no-store"
    return response.status, json.loads(response.body)


@pytest.mark.parametrize("case", ["missing", "forged", "old", "future", "malformed"])
async def test_diary_rejects_untrusted_init_data_before_reading_db(case):
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

    assert await request_diary(app, init_data=init_data) == (
        401,
        {"error": "invalid_authorization"},
    )


async def test_diary_requires_bot_access_even_for_signed_users(diary_db):
    factory = diary_db
    with factory.begin() as session:
        session.add(User(telegram_user_id=1))
        session.add(UserAccess(telegram_user_id=2, is_allowed=True))
    app = create_diary_app(factory, token=TOKEN, admin_user_ids=())

    assert await request_diary(app, init_data=telegram_init_data(1)) == (
        403,
        {"error": "access_denied"},
    )
    assert await request_diary(app, init_data=telegram_init_data(2)) == (
        404,
        {"error": "profile_missing"},
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
    assert payload["metrics"] == ["calories", "protein", "carbs"]
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
    (meal,) = today_data["meals"]
    assert meal["time"] == now.strftime("%H:%M")
    assert meal["complete"]
    assert meal["calories"] == 1000
    assert meal["totals"]["protein"] == {"value": 50, "complete": True}
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
    assert updated["metrics"] == {"calories": 270, "protein": 7.5, "fat": 3, "carbs": 46.5}
    assert await mutate_diary(app, command) == (409, {"error": "dish_changed"})
    status, scaled = await mutate_diary(
        app, {**base, "revision": updated["revision"], "weight": 200}
    )
    assert status == 200
    (updated,) = find_meal(scaled, ids["source"])["dishes"]
    assert updated["metrics"] == {"calories": 360, "protein": 10, "fat": 4, "carbs": 62}
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
    assert updated["metrics"] == {"calories": 300, "protein": 48, "fat": 7.5, "carbs": 30}
    with factory() as session:
        item = session.get(EntryItem, ids["target_dish"])
        assert next(metric.value for metric in item.metrics if metric.metric.code == "fiber") == 6


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
