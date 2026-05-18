from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import EntryItemMetric, EntryType, SupportedMetric
from food_registry_bot.db.repositories import EntryItemCreate, EntryRepository, UserRepository
from food_registry_bot.nutrition import (
    FailedNutritionEstimation,
    SkippedNutritionEstimation,
    StaticNutritionEstimationService,
    StoredEntryNutritionEstimationUseCase,
    SuccessfulNutritionEstimation,
)


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


def test_use_case_saves_metrics_for_supported_saved_items() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=701, username="pipeline_user")
    entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        items=[
            EntryItemCreate(name="гречка", quantity=200, unit="g"),
            EntryItemCreate(name="курица", quantity=150, unit="g"),
        ],
    )
    use_case = StoredEntryNutritionEstimationUseCase(
        session,
        StaticNutritionEstimationService(
            raw_payload=(
                '{"items": ['
                '{"client_item_id": "entry-' + str(entry.id) + ':item-0", "calories": 220, "protein": 7.6, "fat": 2.2, "carbs": 42.8}, '
                '{"client_item_id": "entry-' + str(entry.id) + ':item-1", "calories": 248, "protein": 46.5, "fat": 5.4, "carbs": 0.0}'
                "]}"
            )
        ),
    )

    result = use_case.run(entry_ids=[entry.id])

    assert isinstance(result, SuccessfulNutritionEstimation)
    assert result.entry_ids == [entry.id]
    assert result.estimated_item_count == 2
    assert result.saved_metric_count == 8
    assert session.query(EntryItemMetric).count() == 8


def test_use_case_skips_when_entries_not_found() -> None:
    session = create_test_session()
    use_case = StoredEntryNutritionEstimationUseCase(
        session,
        StaticNutritionEstimationService(raw_payload='{"items": []}'),
    )

    result = use_case.run(entry_ids=[999])

    assert result == SkippedNutritionEstimation(reason="No entries found for nutrition estimation.")


def test_use_case_skips_when_entries_have_no_supported_food_items() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=702, username="skip_user")
    entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="омлет")],
    )
    use_case = StoredEntryNutritionEstimationUseCase(
        session,
        StaticNutritionEstimationService(raw_payload='{"items": []}'),
    )

    result = use_case.run(entry_ids=[entry.id])

    assert result == SkippedNutritionEstimation(
        reason="No supported food items with quantity and unit found for nutrition estimation."
    )


def test_use_case_returns_failed_when_nutrition_payload_is_invalid() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=703, username="invalid_user")
    entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="гречка", quantity=200, unit="g")],
    )
    use_case = StoredEntryNutritionEstimationUseCase(
        session,
        StaticNutritionEstimationService(
            raw_payload='{"items": [{"client_item_id": "entry-1:item-0", "calories": 220}]}'
        ),
    )

    result = use_case.run(entry_ids=[entry.id])

    assert isinstance(result, FailedNutritionEstimation)
    assert "невалидный structured payload" in result.message
    assert session.query(EntryItemMetric).count() == 0
