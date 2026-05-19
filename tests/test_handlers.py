from io import BytesIO
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.handlers import handle_message, handle_recent, handle_start, handle_water_250_ml
from food_registry_bot.bot.keyboards import WATER_250_ML_BUTTON_TEXT
from food_registry_bot.db.base import Base
from food_registry_bot.db.models import Entry, EntryItem, EntryItemMetric, EntryType, SupportedMetric, User
from food_registry_bot.extraction import (
    ExtractedJournalEntry,
    ExtractedJournalItem,
    ExtractedJournalPayload,
    InvalidExtractionPayload,
    ValidExtractionPayload,
)
from food_registry_bot.nutrition import StaticNutritionEstimationService


def create_session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory() as session:
        session.add_all(
            [
                SupportedMetric(code="calories", name="Calories", unit="kcal"),
                SupportedMetric(code="protein", name="Protein", unit="g"),
                SupportedMetric(code="fat", name="Fat", unit="g"),
                SupportedMetric(code="carbs", name="Carbs", unit="g"),
            ]
        )
        session.commit()
    return factory


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


async def test_start_creates_user_on_first_message() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1001, username="new_user"),
        answer=AsyncMock(),
    )

    await handle_start(message, session_factory)

    with session_factory() as session:
        saved_user = session.query(User).filter_by(telegram_user_id=1001).one()

    assert saved_user.username == "new_user"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Привет. Профиль создан, бот готов принимать записи.",)
    assert message.answer.await_args.kwargs["reply_markup"] is not None


async def test_regular_message_reuses_existing_user_and_saves_metrics() -> None:
    session_factory = create_session_factory()
    with session_factory() as session:
        session.add(User(telegram_user_id=1002, username="known_user", timezone="Europe/Moscow"))
        session.commit()

    extraction_service = SimpleNamespace(
        extract=lambda _request: ValidExtractionPayload(
            payload=ExtractedJournalPayload(
                entries=[
                    ExtractedJournalEntry(
                        type=EntryType.FOOD,
                        items=[ExtractedJournalItem(name="яблоко", quantity=180, unit="г")],
                    )
                ]
            ),
            extraction_provider="openai_responses",
            extraction_model="gpt-5-mini",
            raw_payload='{"entries":[{"type":"food","items":[{"name":"яблоко","quantity":180,"unit":"г"}]}]}',
        )
    )
    nutrition_service = StaticNutritionEstimationService(
        raw_payload=build_metric_payload(["entry-1:item-0"])
    )
    message = SimpleNamespace(
        text="яблоко",
        from_user=SimpleNamespace(id=1002, username="known_user"),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory, extraction_service=extraction_service, nutrition_service=nutrition_service)

    with session_factory() as session:
        users_count = session.query(User).filter_by(telegram_user_id=1002).count()
        saved_entry = session.query(Entry).filter_by(user_id=1).one()
        saved_item = session.query(EntryItem).filter_by(entry_id=saved_entry.id).one()
        metric_count = session.query(EntryItemMetric).count()

    assert users_count == 1
    assert saved_entry.entry_type == EntryType.FOOD
    assert saved_entry.source_text is None
    assert saved_item.name == "яблоко"
    assert metric_count == 4
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- яблоко: 180 г\n\nКБЖУ по еде:\n- калории: 220.0 ккал\n- белки: 7.6 г\n- жиры: 2.2 г\n- углеводы: 42.8 г",
    )


async def test_first_regular_message_creates_user_and_food_entry_via_extraction() -> None:
    session_factory = create_session_factory()
    extraction_service = SimpleNamespace(
        extract=lambda _request: ValidExtractionPayload(
            payload=ExtractedJournalPayload(
                entries=[
                    ExtractedJournalEntry(
                        type=EntryType.FOOD,
                        items=[ExtractedJournalItem(name="гречка с курицей")],
                    )
                ]
            ),
            extraction_provider="openai_responses",
            extraction_model="gpt-5-mini",
            raw_payload='{"entries":[{"type":"food","items":[{"name":"гречка с курицей"}]}]}',
        )
    )
    nutrition_service = StaticNutritionEstimationService(
        raw_payload=build_metric_payload(["entry-1:item-0"], confidence="low")
    )
    message = SimpleNamespace(
        text="гречка с курицей",
        from_user=SimpleNamespace(id=1004, username="food_user"),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory, extraction_service=extraction_service, nutrition_service=nutrition_service)

    with session_factory() as session:
        saved_user = session.query(User).filter_by(telegram_user_id=1004).one()
        saved_entry = session.query(Entry).filter_by(user_id=saved_user.id).one()
        saved_item = session.query(EntryItem).filter_by(entry_id=saved_entry.id).one()
        saved_metrics = session.query(EntryItemMetric).all()

    assert saved_entry.entry_type == EntryType.FOOD
    assert saved_entry.source_text is None
    assert saved_item.name == "гречка с курицей"
    assert len(saved_metrics) == 4
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- гречка с курицей\n\nКБЖУ по еде:\n- калории: 220.0 ккал\n- белки: 7.6 г\n- жиры: 2.2 г\n- углеводы: 42.8 г",
    )


async def test_json_message_creates_food_and_water_entries_and_saves_food_metrics() -> None:
    session_factory = create_session_factory()
    nutrition_service = StaticNutritionEstimationService(
        raw_payload=build_metric_payload(["entry-1:item-0"])
    )
    message = SimpleNamespace(
        text=(
            '{"entries": ['
            '{"type": "food", "items": [{"name": "гречка", "quantity": 200, "unit": "г"}]}, '
            '{"type": "water", "items": [{"name": "вода", "quantity": 250, "unit": "мл"}]}'
            "]}"
        ),
        from_user=SimpleNamespace(id=1005, username="json_user"),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory, nutrition_service=nutrition_service)

    with session_factory() as session:
        saved_user = session.query(User).filter_by(telegram_user_id=1005).one()
        saved_entries = session.query(Entry).filter_by(user_id=saved_user.id).order_by(Entry.id).all()
        saved_items = session.query(EntryItem).order_by(EntryItem.id).all()
        metric_count = session.query(EntryItemMetric).count()

    assert [entry.entry_type for entry in saved_entries] == [EntryType.FOOD, EntryType.WATER]
    assert saved_entries[0].extraction_provider == "structured_payload"
    assert saved_items[0].name == "гречка"
    assert saved_items[1].name == "water"
    assert metric_count == 4
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- гречка: 200 г\n- вода: 250 мл\n\nКБЖУ по еде:\n- калории: 220.0 ккал\n- белки: 7.6 г\n- жиры: 2.2 г\n- углеводы: 42.8 г",
    )


async def test_invalid_json_message_returns_validation_error() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        text='{"entries": [{"type": "food"}]}',
        from_user=SimpleNamespace(id=1006, username="bad_json_user"),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory)

    with session_factory() as session:
        entries_count = session.query(Entry).count()

    assert entries_count == 0
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Не удалось разобрать structured payload. Ожидаю объект вида {'entries': [...]} с type, items и name.",
    )


async def test_recent_returns_latest_entries_for_user() -> None:
    session_factory = create_session_factory()
    with session_factory() as session:
        user = User(telegram_user_id=1007, username="recent_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()

        first_entry = Entry(user_id=user.id, entry_type=EntryType.FOOD, source_text="яблоко", occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc))
        second_entry = Entry(user_id=user.id, entry_type=EntryType.WATER, source_text="250 мл", occurred_at=datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc))
        session.add_all([first_entry, second_entry])
        session.flush()

        session.add(EntryItem(entry_id=first_entry.id, position=0, name="яблоко"))
        session.add(EntryItem(entry_id=second_entry.id, position=0, name="water", quantity=250, unit="ml"))
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1007, username="recent_user"),
        answer=AsyncMock(),
    )

    await handle_recent(message, session_factory)

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Последние записи:\n- вода: 250 мл\n- яблоко",
    )


async def test_recent_returns_empty_state_when_no_entries_exist() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1008, username="empty_recent_user"),
        answer=AsyncMock(),
    )

    await handle_recent(message, session_factory)

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Пока нет сохранённых записей.",)


async def test_water_button_creates_water_entry() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        text=WATER_250_ML_BUTTON_TEXT,
        from_user=SimpleNamespace(id=1003, username="water_user"),
        answer=AsyncMock(),
    )

    await handle_water_250_ml(message, session_factory)

    with session_factory() as session:
        saved_user = session.query(User).filter_by(telegram_user_id=1003).one()
        saved_entry = session.query(Entry).filter_by(user_id=saved_user.id).one()
        saved_item = session.query(EntryItem).filter_by(entry_id=saved_entry.id).one()

    assert saved_entry.entry_type == EntryType.WATER
    assert saved_entry.source_text == "250 мл"
    assert saved_item.name == "water"
    assert saved_item.quantity == 250
    assert saved_item.unit == "ml"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Сохранил:\n- вода: 250 мл",)


async def test_photo_message_creates_entries_and_food_metrics() -> None:
    session_factory = create_session_factory()
    extraction_service = SimpleNamespace(
        extract=lambda _request: ValidExtractionPayload(
            payload=ExtractedJournalPayload(
                entries=[
                    ExtractedJournalEntry(
                        type=EntryType.FOOD,
                        items=[
                            ExtractedJournalItem(name="омлет"),
                            ExtractedJournalItem(name="тост"),
                        ],
                    )
                ]
            ),
            extraction_provider="openai_responses",
            extraction_model="gpt-5-mini",
            raw_payload='{"entries":[{"type":"food","items":[{"name":"омлет"},{"name":"тост"}]}]}',
        )
    )
    nutrition_service = StaticNutritionEstimationService(
        raw_payload=build_metric_payload(["entry-1:item-0", "entry-1:item-1"], confidence="low")
    )
    message = SimpleNamespace(
        text=None,
        caption="омлет с тостом",
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")],
        from_user=SimpleNamespace(id=1009, username="photo_user"),
        bot=SimpleNamespace(download=AsyncMock(return_value=BytesIO(b"image-bytes"))),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory, extraction_service=extraction_service, nutrition_service=nutrition_service)

    with session_factory() as session:
        saved_user = session.query(User).filter_by(telegram_user_id=1009).one()
        saved_entry = session.query(Entry).filter_by(user_id=saved_user.id).one()
        saved_items = session.query(EntryItem).filter_by(entry_id=saved_entry.id).order_by(EntryItem.id).all()
        metric_count = session.query(EntryItemMetric).count()

    assert saved_entry.entry_type == EntryType.FOOD
    assert saved_entry.extraction_provider == "openai_responses"
    assert [(item.name, item.source_type) for item in saved_items] == [
        ("омлет", "extraction_payload"),
        ("тост", "extraction_payload"),
    ]
    assert metric_count == 8
    message.bot.download.assert_awaited_once()
    assert message.bot.download.await_args.args[0].file_id == "large"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- омлет\n- тост\n\nКБЖУ по еде:\n- калории: 441.0 ккал\n- белки: 16.2 г\n- жиры: 5.4 г\n- углеводы: 86.6 г",
    )


async def test_empty_message_returns_updated_unsupported_text() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        text=None,
        caption=None,
        photo=[],
        from_user=SimpleNamespace(id=1010, username="empty_user"),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory)

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Пока поддерживаются текстовые сообщения, фото еды и кнопка воды.",
    )


async def test_photo_message_returns_provider_unsupported_error_when_extraction_not_attempted() -> None:
    session_factory = create_session_factory()
    extraction_service = SimpleNamespace(extract=lambda _request: None)
    message = SimpleNamespace(
        text=None,
        caption=None,
        photo=[SimpleNamespace(file_id="large")],
        from_user=SimpleNamespace(id=1011, username="photo_user"),
        bot=SimpleNamespace(download=AsyncMock(return_value=BytesIO(b"image-bytes"))),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory, extraction_service=extraction_service)

    with session_factory() as session:
        entries_count = session.query(Entry).count()

    assert entries_count == 0
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Текущий extraction provider не смог обработать сообщение с едой.",
    )


async def test_handler_rolls_back_food_write_when_nutrition_payload_is_invalid() -> None:
    session_factory = create_session_factory()
    extraction_service = SimpleNamespace(
        extract=lambda _request: ValidExtractionPayload(
            payload=ExtractedJournalPayload(
                entries=[
                    ExtractedJournalEntry(
                        type=EntryType.FOOD,
                        items=[ExtractedJournalItem(name="гречка", quantity=200, unit="г")],
                    )
                ]
            ),
            extraction_provider="openai_responses",
            extraction_model="gpt-5-mini",
            raw_payload='{"entries":[{"type":"food","items":[{"name":"гречка","quantity":200,"unit":"г"}]}]}',
        )
    )
    nutrition_service = StaticNutritionEstimationService(
        raw_payload='{"items": [{"client_item_id": "entry-1:item-0", "metrics": []}]}'
    )
    message = SimpleNamespace(
        text="гречка",
        from_user=SimpleNamespace(id=1012, username="rollback_user"),
        answer=AsyncMock(),
    )

    await handle_message(message, session_factory, extraction_service=extraction_service, nutrition_service=nutrition_service)

    with session_factory() as session:
        assert session.query(Entry).count() == 0
        assert session.query(EntryItemMetric).count() == 0

    message.answer.assert_awaited_once()
    assert "невалидный structured payload" in message.answer.await_args.args[0]
