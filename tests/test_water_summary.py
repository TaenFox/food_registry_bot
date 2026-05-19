from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import EntryType, SupportedMetric
from food_registry_bot.db.repositories import EntryItemCreate, EntryRepository, UserRepository
from food_registry_bot.nutrition import DailyWaterSummaryUseCase


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
        ]
    )
    session.commit()
    return session


def test_daily_water_summary_uses_same_nutrition_day_bounds() -> None:
    session = create_test_session()
    user = UserRepository(session).create(
        telegram_user_id=9001,
        username="water_day_user",
        timezone="Europe/Moscow",
    )
    repository = EntryRepository(session)
    repository.create(
        user_id=user.id,
        entry_type=EntryType.WATER,
        occurred_at=datetime(2026, 5, 19, 0, 30, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="water", quantity=250, unit="ml")],
    )
    repository.create(
        user_id=user.id,
        entry_type=EntryType.WATER,
        occurred_at=datetime(2026, 5, 19, 3, 30, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="water", quantity=500, unit="ml")],
    )
    session.commit()

    summary = DailyWaterSummaryUseCase(session).run(
        user_id=user.id,
        timezone_name="Europe/Moscow",
        summary_date=datetime(2026, 5, 19, tzinfo=timezone.utc).date(),
        nutrition_day_start_hour=6,
    )

    assert summary.total_ml == 500
    assert summary.included_entry_count == 1
    assert summary.excluded_entry_count == 0
    assert summary.is_complete is True


def test_daily_water_summary_excludes_invalid_water_entries() -> None:
    session = create_test_session()
    user = UserRepository(session).create(
        telegram_user_id=9002,
        username="water_invalid_user",
        timezone="Europe/Moscow",
    )
    repository = EntryRepository(session)
    repository.create(
        user_id=user.id,
        entry_type=EntryType.WATER,
        occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="water", quantity=250, unit="ml")],
    )
    repository.create(
        user_id=user.id,
        entry_type=EntryType.WATER,
        occurred_at=datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="tea", quantity=250, unit="ml")],
    )
    repository.create(
        user_id=user.id,
        entry_type=EntryType.WATER,
        occurred_at=datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="water", quantity=1, unit="l")],
    )
    session.commit()

    summary = DailyWaterSummaryUseCase(session).run(
        user_id=user.id,
        timezone_name="Europe/Moscow",
        summary_date=datetime(2026, 5, 19, tzinfo=timezone.utc).date(),
        nutrition_day_start_hour=4,
    )

    assert summary.total_ml == 250
    assert summary.included_entry_count == 1
    assert summary.excluded_entry_count == 2
    assert summary.is_complete is False
