from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
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

TOKEN = "12345:test-token"


@pytest.fixture
def diary_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'diary.sqlite3'}")
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


async def request_diary(app, *, init_data=None, query=""):
    headers = {"Authorization": f"tma {init_data}"} if init_data is not None else {}
    request = make_mocked_request("GET", "/api/diary" + query, headers=headers, app=app)
    route = await app.router.resolve(request)
    response = await route.handler(request)
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
    }
    assert by_date[yesterday.isoformat()]["metrics"]["calories"]["percent"] == 40
    assert today_data["meals"] == [
        {
            "time": now.strftime("%H:%M"),
            "complete": True,
            "calories": 1000,
            "dishes": [{"name": "Рис", "calories": 500}, {"name": "Тунец", "calories": 500}],
        }
    ]
    imported = by_date[older.isoformat()]
    assert imported["metrics"]["calories"]["percent"] is None
    assert not imported["complete"]
    assert not imported["meals"][0]["complete"]
    assert imported["meals"][0]["dishes"][1]["calories"] is None
    with factory() as session:
        assert session.query(DailyGoalSnapshot).count() == 2
