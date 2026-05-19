from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import EntryItemMetric, EntryType, SupportedMetric
from food_registry_bot.db.repositories import EntryItemCreate, EntryRepository, UserRepository
from food_registry_bot.nutrition import (
    BackfillNutritionEstimationUseCase,
    FailedNutritionEstimation,
    NutritionBackfillCompleted,
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


def build_metric_payload(item_ids: list[str], *, confidence: str = "medium") -> str:
    import json

    items = []
    for index, item_id in enumerate(item_ids):
        items.append(
            {
                "client_item_id": item_id,
                "metrics": [
                    {"code": "calories", "value": 220.0 + index, "confidence": confidence},
                    {"code": "protein", "value": 7.6 + index, "confidence": confidence},
                    {"code": "fat", "value": 2.2 + index, "confidence": confidence},
                    {"code": "carbs", "value": 42.8 + index, "confidence": confidence},
                ],
            }
        )
    return json.dumps({"items": items}, ensure_ascii=False)


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
            raw_payload=build_metric_payload([f"entry-{entry.id}:item-0", f"entry-{entry.id}:item-1"])
        ),
    )

    result = use_case.run(entry_ids=[entry.id])

    assert isinstance(result, SuccessfulNutritionEstimation)
    assert result.entry_ids == [entry.id]
    assert result.estimated_item_count == 2
    assert result.saved_metric_count == 8
    assert result.metric_totals == {
        "calories": 441.0,
        "protein": 16.2,
        "fat": 5.4,
        "carbs": 86.6,
    }
    assert session.query(EntryItemMetric).count() == 8


def test_use_case_skips_when_entries_not_found() -> None:
    session = create_test_session()
    use_case = StoredEntryNutritionEstimationUseCase(
        session,
        StaticNutritionEstimationService(raw_payload='{"items": []}'),
    )

    result = use_case.run(entry_ids=[999])

    assert result == SkippedNutritionEstimation(reason="No entries found for nutrition estimation.")


def test_use_case_estimates_item_without_quantity() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=702, username="estimate_user")
    entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="омлет")],
    )
    use_case = StoredEntryNutritionEstimationUseCase(
        session,
        StaticNutritionEstimationService(
            raw_payload=build_metric_payload([f"entry-{entry.id}:item-0"], confidence="low")
        ),
    )

    result = use_case.run(entry_ids=[entry.id])

    assert isinstance(result, SuccessfulNutritionEstimation)
    assert result.saved_metric_count == 4


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
            raw_payload='{"items": [{"client_item_id": "entry-1:item-0", "metrics": []}]}'
        ),
    )

    result = use_case.run(entry_ids=[entry.id])

    assert isinstance(result, FailedNutritionEstimation)
    assert "невалидный structured payload" in result.message
    assert session.query(EntryItemMetric).count() == 0


def test_backfill_use_case_recomputes_only_incomplete_entries() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=704, username="backfill_user")
    complete_entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="омлет")],
    )
    incomplete_entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="тост")],
    )

    complete_item = complete_entry.items[0]
    session.add_all(
        [
            EntryItemMetric(entry_item_id=complete_item.id, metric_id=1, value=100.0, confidence="medium"),
            EntryItemMetric(entry_item_id=complete_item.id, metric_id=2, value=5.0, confidence="medium"),
            EntryItemMetric(entry_item_id=complete_item.id, metric_id=3, value=4.0, confidence="medium"),
            EntryItemMetric(entry_item_id=complete_item.id, metric_id=4, value=10.0, confidence="medium"),
        ]
    )
    session.commit()

    use_case = BackfillNutritionEstimationUseCase(
        session,
        StaticNutritionEstimationService(
            raw_payload=build_metric_payload([f"entry-{incomplete_entry.id}:item-0"], confidence="low")
        ),
    )

    result = use_case.run(limit=10)

    assert isinstance(result, NutritionBackfillCompleted)
    assert result.selected_entry_ids == [incomplete_entry.id]
    assert result.processed_entry_ids == [incomplete_entry.id]
    assert result.skipped_entry_ids == []
    assert result.failed_entries == []
    assert result.saved_metric_count == 4
