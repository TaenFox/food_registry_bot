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
    SupportedDiet,
    SupportedMetric,
    User,
    UserDietPreference,
    UserGoalPreference,
)
from food_registry_bot.db.repositories import SupportedDietRepository


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
                SupportedDiet(code="low_purine", name="Низкопуриновая", is_enabled=True),
                SupportedMetric(code="calories", name="Calories", unit="kcal"),
                SupportedMetric(code="protein", name="Protein", unit="g"),
                SupportedMetric(code="fat", name="Fat", unit="g"),
                SupportedMetric(code="carbs", name="Carbs", unit="g"),
                SupportedMetric(code="fiber", name="Fiber", unit="g"),
                SupportedMetric(code="workout_calories", name="Workout Calories", unit="kcal"),
                SupportedMetric(code="workout_calorie_credit", name="Workout Calorie Credit", unit="kcal"),
                SupportedMetric(code="low_purine_score", name="Low Purine Score", unit="score"),
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
        low_purine_diet = SupportedDietRepository(session).get_by_code(code="low_purine")
        assert low_purine_diet is not None
        session.add(UserDietPreference(user_id=user.id, diet_id=low_purine_diet.id, is_enabled=True))

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
            workout_logging_enabled=False,
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
    assert [(diet.code, diet.name) for diet in context.active_diets] == [("low_purine", "Низкопуриновая")]
    assert len(context.recent_entries) == 2
    assert context.recent_entries[0].entry_type == "water"
    assert context.recent_entries[0].rendered_items == ["вода: 500 мл"]
    assert context.recent_entries[1].rendered_items == ["омлет: 250 г"]
    assert context.workout_entries == []


def test_nutrition_coach_context_builder_includes_workout_entries_for_day_when_enabled() -> None:
    session_factory = create_session_factory()
    with session_factory() as session:
        user = User(telegram_user_id=1002, username="workout_coach_user", timezone="Europe/Moscow")
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

        workout_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.WORKOUT,
            source_text="сегодня была пробежка 40 минут",
            occurred_at=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
        )
        old_workout_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.WORKOUT,
            source_text="вчера была силовая 50 минут",
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        session.add_all([workout_entry, old_workout_entry])
        session.flush()
        session.add_all(
            [
                EntryItem(entry_id=workout_entry.id, position=0, name="бег", quantity=40, unit="min"),
                EntryItem(entry_id=old_workout_entry.id, position=0, name="силовая", quantity=50, unit="min"),
            ]
        )
        session.flush()
        current_workout_item = session.query(EntryItem).filter_by(entry_id=workout_entry.id, position=0).one()
        session.add_all(
            [
                EntryItemMetric(
                    entry_item_id=current_workout_item.id,
                    metric_id=6,
                    value=757.0,
                    confidence="high",
                ),
                EntryItemMetric(
                    entry_item_id=current_workout_item.id,
                    metric_id=7,
                    value=250.0,
                    confidence="high",
                ),
            ]
        )
        session.commit()

        context = NutritionCoachContextBuilder(session).build(
            user_id=user.id,
            timezone_name=user.timezone,
            nutrition_day_start_hour=4,
            reference_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
            workout_logging_enabled=True,
        )

    assert len(context.workout_entries) == 1
    assert context.workout_entries[0].source_text == "сегодня была пробежка 40 минут"
    assert context.goal_progress["calories"].goal_value == 2250
    assert context.workout_entries[0].metric_values == {
        "workout_calories": 757.0,
        "workout_calorie_credit": 250.0,
    }
    assert context.workout_entries[0].items[0].name == "бег"
    assert context.workout_entries[0].items[0].unit == "min"
    assert context.workout_entries[0].items[0].rendered_value == "бег: 40 мин"
