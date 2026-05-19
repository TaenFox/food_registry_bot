from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import EntryItem, EntryItemMetric, EntryType, SupportedMetric, UserAccess
from food_registry_bot.db.repositories import (
    EntryItemCreate,
    EntryItemMetricRepository,
    EntryItemMetricValue,
    EntryRepository,
    NutritionEstimatePersistenceService,
    SupportedMetricRepository,
    UserAccessRepository,
    UserRepository,
)
from food_registry_bot.extraction import ExtractedJournalEntry, ExtractedJournalItem, ExtractedJournalPayload
from food_registry_bot.nutrition import (
    StaticNutritionEstimationService,
    ValidNutritionPayload,
    prepare_nutrition_request_from_entries,
    prepare_nutrition_request_from_extracted_payload,
    resolve_nutrition_estimates,
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


def test_user_repository_creates_user_once() -> None:
    session = create_test_session()
    repository = UserRepository(session)

    user, created = repository.get_or_create(telegram_user_id=101, username="alice")
    same_user, created_again = repository.get_or_create(telegram_user_id=101, username="alice")

    assert created is True
    assert created_again is False
    assert user.id == same_user.id
    assert user.username == "alice"


def test_user_access_repository_sets_and_updates_access() -> None:
    session = create_test_session()
    repository = UserAccessRepository(session)

    allowed_access = repository.set_access(
        telegram_user_id=9001,
        username="first_user",
        is_allowed=True,
    )
    denied_access = repository.set_access(
        telegram_user_id=9001,
        username="first_user_renamed",
        is_allowed=False,
    )

    assert allowed_access.id == denied_access.id
    assert repository.is_allowed(9001) is False
    saved_access = session.query(UserAccess).filter_by(telegram_user_id=9001).one()
    assert saved_access.username == "first_user_renamed"
    assert saved_access.is_allowed is False


def test_user_access_repository_lists_known_users_from_profiles_and_access() -> None:
    session = create_test_session()
    UserRepository(session).create(telegram_user_id=7001, username="profile_only")
    UserRepository(session).create(telegram_user_id=7002, username="allowed_user")
    repository = UserAccessRepository(session)
    repository.set_access(
        telegram_user_id=7002,
        username="allowed_user",
        is_allowed=True,
    )
    repository.set_access(
        telegram_user_id=7003,
        username="denied_user",
        is_allowed=False,
    )

    known_users = repository.list_known_users()

    assert [
        (
            known_user.telegram_user_id,
            known_user.username,
            known_user.has_profile,
            known_user.is_allowed,
        )
        for known_user in known_users
    ] == [
        (7001, "profile_only", True, False),
        (7002, "allowed_user", True, True),
        (7003, "denied_user", False, False),
    ]


def test_entry_repository_creates_entry_for_user() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=202, username="bob")

    entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        source_text="яблоко",
    )

    assert entry.id is not None
    assert entry.user_id == user.id
    assert entry.entry_type == EntryType.FOOD
    assert entry.source_text == "яблоко"


def test_entry_repository_persists_extraction_trace() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=252, username="trace")

    entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        extraction_provider="openai_responses",
        extraction_model="gpt-5-mini",
        extraction_raw_payload='{"entries":[{"type":"food","items":[{"name":"яблоко"}]}]}',
        items=[EntryItemCreate(name="яблоко")],
    )

    assert entry.extraction_provider == "openai_responses"
    assert entry.extraction_model == "gpt-5-mini"
    assert entry.extraction_raw_payload == '{"entries":[{"type":"food","items":[{"name":"яблоко"}]}]}'


def test_entry_repository_creates_water_entry() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=303, username="water")

    entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.WATER,
        occurred_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        source_text="250 мл",
        items=[EntryItemCreate(name="water", quantity=250, unit="ml")],
    )

    item = session.query(EntryItem).filter_by(entry_id=entry.id).one()

    assert entry.user_id == user.id
    assert entry.entry_type == EntryType.WATER
    assert entry.source_text == "250 мл"
    assert item.position == 0
    assert item.name == "water"
    assert item.quantity == 250
    assert item.unit == "ml"


def test_entry_repository_lists_recent_entries_in_descending_order() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=404, username="recent")
    repository = EntryRepository(session)

    older_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        source_text="яблоко",
        items=[EntryItemCreate(name="яблоко")],
    )
    newer_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.WATER,
        occurred_at=datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc),
        source_text="250 мл",
        items=[EntryItemCreate(name="water", quantity=250, unit="ml")],
    )

    recent_entries = repository.list_recent_for_user(user_id=user.id, limit=5)

    assert [entry.id for entry in recent_entries] == [newer_entry.id, older_entry.id]


def test_entry_repository_lists_incomplete_food_entry_ids() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=405, username="incomplete")
    repository = EntryRepository(session)

    complete_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="яблоко")],
    )
    incomplete_entry = repository.create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="гречка")],
    )

    complete_item = session.query(EntryItem).filter_by(entry_id=complete_entry.id).one()
    EntryItemMetricRepository(session).upsert_metrics(
        entry_item_id=complete_item.id,
        metric_values=[
            EntryItemMetricValue(code="calories", value=100.0, confidence="medium"),
            EntryItemMetricValue(code="protein", value=2.0, confidence="medium"),
            EntryItemMetricValue(code="fat", value=1.0, confidence="medium"),
            EntryItemMetricValue(code="carbs", value=20.0, confidence="medium"),
        ],
    )

    incomplete_ids = repository.list_incomplete_food_entry_ids(
        required_metric_codes=["calories", "protein", "fat", "carbs"],
        limit=10,
    )

    assert incomplete_ids == [incomplete_entry.id]


def test_supported_metric_repository_lists_seeded_metrics() -> None:
    session = create_test_session()

    metrics = SupportedMetricRepository(session).list_all()

    assert [metric.code for metric in metrics] == ["calories", "protein", "fat", "carbs"]


def test_entry_item_metric_repository_upserts_metric_values() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=505, username="metric_user")
    entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        items=[EntryItemCreate(name="гречка", quantity=200, unit="g")],
    )
    entry_item = session.query(EntryItem).filter_by(entry_id=entry.id, position=0).one()
    repository = EntryItemMetricRepository(session)

    repository.upsert_metrics(
        entry_item_id=entry_item.id,
        metric_values=[
            EntryItemMetricValue(code="calories", value=220.0, confidence="high"),
            EntryItemMetricValue(code="protein", value=7.6, confidence="medium"),
        ],
    )
    repository.upsert_metrics(
        entry_item_id=entry_item.id,
        metric_values=[
            EntryItemMetricValue(code="calories", value=230.0, confidence="medium"),
            EntryItemMetricValue(code="protein", value=8.1, confidence="low"),
        ],
    )

    saved_metrics = (
        session.query(EntryItemMetric)
        .join(SupportedMetric, SupportedMetric.id == EntryItemMetric.metric_id)
        .filter(EntryItemMetric.entry_item_id == entry_item.id)
        .order_by(SupportedMetric.code.asc())
        .all()
    )

    assert len(saved_metrics) == 2
    assert [(metric.metric.code, metric.value, metric.confidence) for metric in saved_metrics] == [
        ("calories", 230.0, "medium"),
        ("protein", 8.1, "low"),
    ]


def test_nutrition_persistence_service_saves_metrics_for_saved_entries() -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=606, username="nutrition_user")
    entry = EntryRepository(session).create(
        user_id=user.id,
        entry_type=EntryType.FOOD,
        occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        items=[
            EntryItemCreate(name="гречка", quantity=200, unit="g"),
            EntryItemCreate(name="курица", quantity=150, unit="g"),
        ],
    )

    prepared_request = prepare_nutrition_request_from_entries([entry])
    assert prepared_request is not None

    nutrition_result = StaticNutritionEstimationService(
        raw_payload=build_metric_payload(
            [f"entry-{entry.id}:item-0", f"entry-{entry.id}:item-1"],
            confidence="medium",
        )
    ).estimate(prepared_request.request)
    assert isinstance(nutrition_result, ValidNutritionPayload)
    resolved_estimates = resolve_nutrition_estimates(prepared_request, nutrition_result.payload)

    saved_metrics = NutritionEstimatePersistenceService(session).save_resolved_estimates_for_entries(
        prepared_request=prepared_request,
        resolved_estimates=resolved_estimates,
    )

    assert len(saved_metrics) == 8
    persisted_metrics = (
        session.query(EntryItemMetric)
        .join(SupportedMetric, SupportedMetric.id == EntryItemMetric.metric_id)
        .order_by(EntryItemMetric.entry_item_id.asc(), SupportedMetric.code.asc())
        .all()
    )
    assert len(persisted_metrics) == 8
    assert [
        (metric.entry_item.position, metric.metric.code, metric.value, metric.confidence)
        for metric in persisted_metrics
    ] == [
        (0, "calories", 220.0, "medium"),
        (0, "carbs", 42.8, "medium"),
        (0, "fat", 2.2, "medium"),
        (0, "protein", 7.6, "medium"),
        (1, "calories", 221.0, "medium"),
        (1, "carbs", 43.8, "medium"),
        (1, "fat", 3.2, "medium"),
        (1, "protein", 8.6, "medium"),
    ]


def test_prepare_and_persist_extracted_payload_requires_saved_entry_items() -> None:
    session = create_test_session()
    prepared_request = prepare_nutrition_request_from_extracted_payload(
        ExtractedJournalPayload(
            entries=[
                ExtractedJournalEntry(
                    type=EntryType.FOOD,
                    items=[ExtractedJournalItem(name="гречка", quantity=200, unit="г")],
                )
            ]
        )
    )
    assert prepared_request is not None
    nutrition_result = StaticNutritionEstimationService(
        raw_payload=build_metric_payload(["entry-0:item-0"])
    ).estimate(prepared_request.request)
    assert isinstance(nutrition_result, ValidNutritionPayload)
    resolved_estimates = resolve_nutrition_estimates(prepared_request, nutrition_result.payload)

    with pytest.raises(ValueError, match="was not found"):
        NutritionEstimatePersistenceService(session).save_resolved_estimates_for_entries(
            prepared_request=prepared_request,
            resolved_estimates=resolved_estimates,
        )
