from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import EntryItemMetric, EntryType, SupportedMetric
from food_registry_bot.db.repositories import EntryItemCreate, EntryRepository, UserRepository
from food_registry_bot.nutrition import DailyNutritionSummaryUseCase, resolve_local_summary_date


def create_test_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)()
    session.add_all(
        [
            SupportedMetric(code="calories", name="Calories", unit="kcal"),
            SupportedMetric(code="protein", name="Protein", unit="g"),
            SupportedMetric(code="fat", name="Fat", unit="g"),
            SupportedMetric(code="carbs", name="Carbs", unit="g"),
            SupportedMetric(code="fiber", name="Fiber", unit="g"),
            SupportedMetric(code="workout_calories", name="Workout Calories", unit="kcal"),
            SupportedMetric(code="workout_calorie_credit", name="Workout Calorie Credit", unit="kcal"),
        ]
    )
    session.commit()
    return session


def save_metrics(entry, *, values: tuple[float, float, float, float, float]) -> None:
    calories, protein, fat, carbs, fiber = values
    item = entry.items[0]
    item.metrics.extend(
        [
            EntryItemMetric(entry_item_id=item.id, metric_id=1, value=calories, confidence="medium"),
            EntryItemMetric(entry_item_id=item.id, metric_id=2, value=protein, confidence="medium"),
            EntryItemMetric(entry_item_id=item.id, metric_id=3, value=fat, confidence="medium"),
            EntryItemMetric(entry_item_id=item.id, metric_id=4, value=carbs, confidence="medium"),
            EntryItemMetric(entry_item_id=item.id, metric_id=5, value=fiber, confidence="medium"),
        ]
    )


def test_daily_summary_uses_local_day_boundaries() -> None:
    session = create_test_session()
    user = UserRepository(session).create(
        telegram_user_id=8001,
        username="moscow_user",
        timezone="Europe/Moscow",
    )
    repository = EntryRepository(session)
    previous_local_day_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 19, 0, 30, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="ужин")],
    )
    today_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 19, 1, 30, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="завтрак")],
    )
    next_local_day_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 20, 1, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="поздний ужин")],
    )
    save_metrics(previous_local_day_entry, values=(100.0, 10.0, 5.0, 7.0, 3.0))
    save_metrics(today_entry, values=(200.0, 20.0, 8.0, 15.0, 6.0))
    save_metrics(next_local_day_entry, values=(300.0, 30.0, 9.0, 18.0, 8.0))
    session.commit()

    summary_date = resolve_local_summary_date(
        reference_at=datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc),
        timezone_name=user.timezone,
        nutrition_day_start_hour=4,
    )
    summary = DailyNutritionSummaryUseCase(session).run(
        user_id=user.id,
        timezone_name=user.timezone,
        summary_date=summary_date,
        nutrition_day_start_hour=4,
    )

    assert summary.summary_date.isoformat() == "2026-05-19"
    assert summary.included_entry_count == 1
    assert summary.excluded_entry_count == 0
    assert [entry.entry_id for entry in summary.entries] == [today_entry.id]
    assert summary.totals.calories == 200.0
    assert summary.totals.protein == 20.0
    assert summary.totals.fat == 8.0
    assert summary.totals.carbs == 15.0
    assert summary.totals.fiber == 6.0


def test_local_time_before_four_am_belongs_to_previous_nutrition_day() -> None:
    session = create_test_session()
    user = UserRepository(session).create(
        telegram_user_id=8003,
        username="night_user",
        timezone="Europe/Moscow",
    )
    repository = EntryRepository(session)
    night_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 19, 0, 30, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="поздний ужин")],
    )
    morning_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 19, 2, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="завтрак")],
    )
    save_metrics(night_entry, values=(450.0, 25.0, 20.0, 35.0, 9.0))
    save_metrics(morning_entry, values=(300.0, 18.0, 12.0, 22.0, 5.0))
    session.commit()

    previous_day_summary = DailyNutritionSummaryUseCase(session).run(
        user_id=user.id,
        timezone_name=user.timezone,
        summary_date=resolve_local_summary_date(
            reference_at=datetime(2026, 5, 19, 0, 45, tzinfo=timezone.utc),
            timezone_name=user.timezone,
            nutrition_day_start_hour=4,
        ),
        nutrition_day_start_hour=4,
    )
    current_day_summary = DailyNutritionSummaryUseCase(session).run(
        user_id=user.id,
        timezone_name=user.timezone,
        summary_date=resolve_local_summary_date(
            reference_at=datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc),
            timezone_name=user.timezone,
            nutrition_day_start_hour=4,
        ),
        nutrition_day_start_hour=4,
    )

    assert previous_day_summary.summary_date.isoformat() == "2026-05-18"
    assert [entry.entry_id for entry in previous_day_summary.entries] == [night_entry.id]
    assert previous_day_summary.totals.calories == 450.0
    assert current_day_summary.summary_date.isoformat() == "2026-05-19"
    assert [entry.entry_id for entry in current_day_summary.entries] == [morning_entry.id]
    assert current_day_summary.totals.calories == 300.0


def test_daily_summary_excludes_incomplete_food_entries() -> None:
    session = create_test_session()
    user = UserRepository(session).create(
        telegram_user_id=8002,
        username="history_user",
        timezone="Europe/Moscow",
    )
    repository = EntryRepository(session)
    complete_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 19, 7, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="омлет")],
    )
    incomplete_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="тост")],
    )
    save_metrics(complete_entry, values=(250.0, 18.0, 14.0, 12.0, 4.0))
    incomplete_entry.items[0].metrics.extend(
        [
            EntryItemMetric(entry_item_id=incomplete_entry.items[0].id, metric_id=1, value=120.0, confidence="medium"),
            EntryItemMetric(entry_item_id=incomplete_entry.items[0].id, metric_id=2, value=4.0, confidence="medium"),
        ]
    )
    session.commit()

    summary = DailyNutritionSummaryUseCase(session).run(
        user_id=user.id,
        timezone_name=user.timezone,
        summary_date=resolve_local_summary_date(
            reference_at=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
            timezone_name=user.timezone,
            nutrition_day_start_hour=4,
        ),
        nutrition_day_start_hour=4,
    )

    assert summary.is_complete is False
    assert summary.included_entry_count == 1
    assert summary.excluded_entry_count == 1
    assert summary.totals.calories == 250.0
    assert summary.totals.protein == 18.0
    assert summary.totals.fat == 14.0
    assert summary.totals.carbs == 12.0
    assert summary.totals.fiber == 4.0


def test_daily_summary_uses_custom_nutrition_day_start_hour() -> None:
    session = create_test_session()
    user = UserRepository(session).create(
        telegram_user_id=8004,
        username="custom_day_user",
        timezone="Europe/Moscow",
    )
    repository = EntryRepository(session)
    early_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 19, 0, 30, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="ночной перекус")],
    )
    later_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 19, 3, 30, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="поздний завтрак")],
    )
    save_metrics(early_entry, values=(150.0, 10.0, 5.0, 12.0, 3.0))
    save_metrics(later_entry, values=(300.0, 20.0, 8.0, 25.0, 7.0))
    session.commit()

    summary = DailyNutritionSummaryUseCase(session).run(
        user_id=user.id,
        timezone_name=user.timezone,
        summary_date=resolve_local_summary_date(
            reference_at=datetime(2026, 5, 19, 3, 0, tzinfo=timezone.utc),
            timezone_name=user.timezone,
            nutrition_day_start_hour=6,
        ),
        nutrition_day_start_hour=6,
    )

    assert summary.summary_date.isoformat() == "2026-05-19"
    assert [entry.entry_id for entry in summary.entries] == [later_entry.id]
    assert summary.totals.calories == 300.0
    assert summary.totals.fiber == 7.0
