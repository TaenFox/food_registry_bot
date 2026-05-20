from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from food_registry_bot.conversation.context import NutritionCoachContextBuilder
from food_registry_bot.db.base import Base
from food_registry_bot.db.models import (
    Entry,
    EntryItem,
    EntryItemMetric,
    EntryType,
    SupportedMetric,
    User,
    UserGoalPreference,
)


def create_session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory() as session:
        session.add_all(
            [
                SupportedMetric(code="calories", name="Calories", unit="kcal"),
                SupportedMetric(code="protein", name="Protein", unit="g"),
                SupportedMetric(code="fat", name="Fat", unit="g"),
                SupportedMetric(code="carbs", name="Carbs", unit="g"),
                SupportedMetric(code="fiber", name="Fiber", unit="g"),
            ]
        )
        session.commit()
    return factory


def test_nutrition_coach_context_builder_uses_day_facts_and_recent_entries() -> None:
    session_factory = create_session_factory()
    with session_factory() as session:
        user = User(telegram_user_id=1001, username="coach_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        session.add(
            UserGoalPreference(
                user_id=user.id,
                calorie_goal=2000,
                protein_goal=120,
                fat_goal=70,
                carbs_goal=220,
                fiber_goal=30,
                water_goal=2500,
            )
        )

        food_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            source_text="омлет",
            occurred_at=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
        )
        water_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.WATER,
            source_text="500 мл",
            occurred_at=datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc),
        )
        session.add_all([food_entry, water_entry])
        session.flush()

        food_item = EntryItem(entry_id=food_entry.id, position=0, name="омлет", quantity=250, unit="g")
        water_item = EntryItem(entry_id=water_entry.id, position=0, name="water", quantity=500, unit="ml")
        session.add_all([food_item, water_item])
        session.flush()

        session.add_all(
            [
                EntryItemMetric(entry_item_id=food_item.id, metric_id=1, value=400.0, confidence="medium"),
                EntryItemMetric(entry_item_id=food_item.id, metric_id=2, value=28.0, confidence="medium"),
                EntryItemMetric(entry_item_id=food_item.id, metric_id=3, value=24.0, confidence="medium"),
                EntryItemMetric(entry_item_id=food_item.id, metric_id=4, value=8.0, confidence="medium"),
                EntryItemMetric(entry_item_id=food_item.id, metric_id=5, value=3.0, confidence="medium"),
            ]
        )
        session.commit()

        context = NutritionCoachContextBuilder(session).build(
            user_id=user.id,
            timezone_name=user.timezone,
            nutrition_day_start_hour=4,
            reference_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
        )

    assert context.summary_date.isoformat() == "2026-05-20"
    assert context.day_totals == {
        "calories": 400.0,
        "protein": 28.0,
        "fat": 24.0,
        "carbs": 8.0,
        "fiber": 3.0,
        "water": 500.0,
    }
    assert context.goal_progress["protein"].goal_value == 120
    assert context.goal_progress["water"].remaining_value == 2000.0
    assert len(context.recent_entries) == 2
    assert context.recent_entries[0].entry_type == "water"
    assert context.recent_entries[0].rendered_items == ["вода: 500 мл"]
    assert context.recent_entries[1].rendered_items == ["омлет: 250 г"]
