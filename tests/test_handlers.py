from __future__ import annotations

import asyncio
from io import BytesIO
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from food_registry_bot.bot.admin_backfill import AdminBackfillTracker
from food_registry_bot.bot.handlers import (
    handle_admin,
    handle_admin_allow,
    handle_admin_backfill_nutrition,
    handle_admin_deny,
    handle_admin_users,
    handle_health,
    handle_message,
    handle_recent,
    handle_start,
    handle_water_250_ml,
)
from food_registry_bot.bot.keyboards import WATER_250_ML_BUTTON_TEXT
from food_registry_bot.db.base import Base
from food_registry_bot.db.models import Entry, EntryItem, EntryItemMetric, EntryType, SupportedMetric, User, UserAccess
from food_registry_bot.extraction import (
    ExtractedJournalEntry,
    ExtractedJournalItem,
    ExtractedJournalPayload,
    ValidExtractionPayload,
)
from food_registry_bot.nutrition import StaticNutritionEstimationService


ADMIN_ID = 999001
ALLOWED_USER_ID = 1001
DENIED_USER_ID = 2002
LARGE_DENIED_USER_ID = 5517166158


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
            ]
        )
        session.commit()
    return factory


def allow_user(session_factory: sessionmaker[Session], telegram_user_id: int, username: str | None = None) -> None:
    with session_factory() as session:
        session.add(UserAccess(telegram_user_id=telegram_user_id, username=username, is_allowed=True))
        session.commit()


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


async def test_start_denies_unallowed_user() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=DENIED_USER_ID, username="denied_user"),
        answer=AsyncMock(),
    )

    await handle_start(message, session_factory, admin_user_ids=(ADMIN_ID,))

    with session_factory() as session:
        assert session.query(User).count() == 0
        saved_access = session.query(UserAccess).filter_by(telegram_user_id=DENIED_USER_ID).one()

    assert saved_access.username == "denied_user"
    assert saved_access.is_allowed is False
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Доступ к боту не разрешён.",)


async def test_start_creates_user_for_allowed_user() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "allowed_user")
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="allowed_user"),
        answer=AsyncMock(),
    )

    await handle_start(message, session_factory, admin_user_ids=(ADMIN_ID,))

    with session_factory() as session:
        saved_user = session.query(User).filter_by(telegram_user_id=ALLOWED_USER_ID).one()

    assert saved_user.username == "allowed_user"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Привет. Профиль создан, бот готов принимать записи.",)


async def test_health_denies_unallowed_user() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=DENIED_USER_ID, username="denied_user"),
        answer=AsyncMock(),
    )

    await handle_health(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Доступ к боту не разрешён.",)


async def test_admin_allow_sets_user_access() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args=str(ALLOWED_USER_ID))

    await handle_admin_allow(message, command, session_factory, admin_user_ids=(ADMIN_ID,))

    with session_factory() as session:
        access = session.query(UserAccess).filter_by(telegram_user_id=ALLOWED_USER_ID).one()

    assert access.is_allowed is True
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (f"Доступ разрешён для пользователя {ALLOWED_USER_ID}.",)


async def test_admin_deny_sets_user_access_false() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "allowed_user")
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args=str(ALLOWED_USER_ID))

    await handle_admin_deny(message, command, session_factory, admin_user_ids=(ADMIN_ID,))

    with session_factory() as session:
        access = session.query(UserAccess).filter_by(telegram_user_id=ALLOWED_USER_ID).one()

    assert access.is_allowed is False
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (f"Доступ запрещён для пользователя {ALLOWED_USER_ID}.",)


async def test_admin_commands_are_forbidden_for_non_admin() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="allowed_user"),
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args=str(DENIED_USER_ID))

    await handle_admin_allow(message, command, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Команда доступна только администратору.",)


async def test_admin_allow_returns_safe_usage_text_for_missing_argument() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args=None)

    await handle_admin_allow(message, command, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Использование: /admin_allow TELEGRAM_USER_ID",)


async def test_admin_returns_system_overview_and_commands() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "allowed_user")
    with session_factory() as session:
        session.add(UserAccess(telegram_user_id=DENIED_USER_ID, username="denied_user", is_allowed=False))
        user = User(telegram_user_id=ALLOWED_USER_ID, username="allowed_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            source_text="омлет",
            occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        )
        session.add(entry)
        session.flush()
        session.add(EntryItem(entry_id=entry.id, position=0, name="омлет"))
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        answer=AsyncMock(),
    )

    await handle_admin(message, session_factory, backfill_tracker=AdminBackfillTracker(), admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        (
            "Admin dashboard:\n"
            f"- текущий админ: {ADMIN_ID}\n"
            "- админов в конфиге: 1\n"
            "- известных пользователей: 2\n"
            "- разрешённых пользователей: 1\n"
            "- запрещённых пользователей: 1\n"
            "- пользователей с профилем: 1\n"
            "- food entries без полного набора метрик: 1\n"
            "- backfill nutrition: idle\n"
            "\n"
            "Доступные команды:\n"
            "- /admin\n"
            "- /admin_users\n"
            "- /admin_allow TELEGRAM_USER_ID\n"
            "- /admin_deny TELEGRAM_USER_ID\n"
            "- /admin_backfill_nutrition [LIMIT]"
        ),
    )


async def test_admin_overview_excludes_admin_from_user_counters() -> None:
    session_factory = create_session_factory()
    with session_factory() as session:
        session.add(UserAccess(telegram_user_id=ADMIN_ID, username="admin", is_allowed=False))
        session.add(UserAccess(telegram_user_id=DENIED_USER_ID, username="denied_user", is_allowed=False))
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        answer=AsyncMock(),
    )

    await handle_admin(message, session_factory, backfill_tracker=AdminBackfillTracker(), admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        (
            "Admin dashboard:\n"
            f"- текущий админ: {ADMIN_ID}\n"
            "- админов в конфиге: 1\n"
            "- известных пользователей: 1\n"
            "- разрешённых пользователей: 0\n"
            "- запрещённых пользователей: 1\n"
            "- пользователей с профилем: 0\n"
            "- food entries без полного набора метрик: 0\n"
            "- backfill nutrition: idle\n"
            "\n"
            "Доступные команды:\n"
            "- /admin\n"
            "- /admin_users\n"
            "- /admin_allow TELEGRAM_USER_ID\n"
            "- /admin_deny TELEGRAM_USER_ID\n"
            "- /admin_backfill_nutrition [LIMIT]"
        ),
    )


async def test_admin_overview_shows_detailed_backfill_progress() -> None:
    session_factory = create_session_factory()
    tracker = AdminBackfillTracker()
    running_task = asyncio.create_task(asyncio.sleep(1))
    tracker.start(requested_by=ADMIN_ID, limit=20, task=running_task)
    tracker.update_progress(
        selected_entry_count=14,
        processed_entry_count=4,
        skipped_entry_count=1,
        failed_entry_count=2,
    )

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        answer=AsyncMock(),
    )

    await handle_admin(message, session_factory, backfill_tracker=tracker, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert "running (selected 14, processed 4, remaining 7, skipped 1, failed 2, limit 20)" in message.answer.await_args.args[0]
    running_task.cancel()


async def test_admin_is_forbidden_for_non_admin() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="allowed_user"),
        answer=AsyncMock(),
    )

    await handle_admin(message, session_factory, backfill_tracker=AdminBackfillTracker(), admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Команда доступна только администратору.",)


async def test_admin_users_returns_known_users_with_status_and_commands() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "allowed_user")
    with session_factory() as session:
        session.add(User(telegram_user_id=DENIED_USER_ID, username="denied_user", timezone="Europe/Moscow"))
        session.add(UserAccess(telegram_user_id=DENIED_USER_ID, username="denied_user", is_allowed=False))
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        answer=AsyncMock(),
    )

    await handle_admin_users(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Пользователи:\n"
        f"- {ALLOWED_USER_ID} @allowed_user [allowed]\n"
        f"/admin_deny {ALLOWED_USER_ID}\n"
        f"- {DENIED_USER_ID} @denied_user [denied]\n"
        f"/admin_allow {DENIED_USER_ID}\n"
        f"- {ADMIN_ID} [admin]",
    )


async def test_admin_users_shows_new_denied_user_after_first_contact() -> None:
    session_factory = create_session_factory()
    denied_message = SimpleNamespace(
        from_user=SimpleNamespace(id=LARGE_DENIED_USER_ID, username="new_user"),
        answer=AsyncMock(),
    )

    await handle_start(denied_message, session_factory, admin_user_ids=(ADMIN_ID,))

    admin_message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        answer=AsyncMock(),
    )

    await handle_admin_users(admin_message, session_factory, admin_user_ids=(ADMIN_ID,))

    admin_message.answer.assert_awaited_once()
    assert admin_message.answer.await_args.args == (
        "Пользователи:\n"
        f"- {LARGE_DENIED_USER_ID} @new_user [denied]\n"
        f"/admin_allow {LARGE_DENIED_USER_ID}\n"
        f"- {ADMIN_ID} [admin]",
    )


async def test_admin_users_is_forbidden_for_non_admin() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="allowed_user"),
        answer=AsyncMock(),
    )

    await handle_admin_users(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Команда доступна только администратору.",)


async def test_admin_backfill_nutrition_recomputes_incomplete_entries() -> None:
    session_factory = create_session_factory()
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="allowed_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            source_text="омлет",
            occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        )
        session.add(entry)
        session.flush()
        session.add(EntryItem(entry_id=entry.id, position=0, name="омлет"))
        session.commit()

    nutrition_service = StaticNutritionEstimationService(
        raw_payload=build_metric_payload(["entry-1:item-0"], confidence="low")
    )
    backfill_tracker = AdminBackfillTracker()
    bot = SimpleNamespace(send_message=AsyncMock())
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        chat=SimpleNamespace(id=7001),
        bot=bot,
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args=None)

    await handle_admin_backfill_nutrition(
        message,
        command,
        session_factory,
        nutrition_service=nutrition_service,
        backfill_tracker=backfill_tracker,
        admin_user_ids=(ADMIN_ID,),
    )

    await backfill_tracker.task

    with session_factory() as session:
        assert session.query(EntryItemMetric).count() == 4

    assert message.answer.await_count == 1
    assert message.answer.await_args.args == ("Запускаю backfill nutrition. Лимит: 20.",)
    assert bot.send_message.await_args.args == (
        7001,
        "Backfill nutrition завершён.\n"
        "Выбрано entries: 1\n"
        "Обработано entries: 1\n"
        "Сохранено метрик: 4",
    )


async def test_admin_backfill_nutrition_returns_safe_usage_text_for_invalid_limit() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args="abc")

    await handle_admin_backfill_nutrition(
        message,
        command,
        session_factory,
        nutrition_service=StaticNutritionEstimationService(raw_payload='{"items": []}'),
        backfill_tracker=AdminBackfillTracker(),
        admin_user_ids=(ADMIN_ID,),
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Использование: /admin_backfill_nutrition [LIMIT]",)


async def test_admin_backfill_nutrition_reports_unhandled_error() -> None:
    session_factory = create_session_factory()

    class FailingNutritionService:
        def estimate(self, request):
            raise RuntimeError("boom")

    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="allowed_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            source_text="омлет",
            occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
        )
        session.add(entry)
        session.flush()
        session.add(EntryItem(entry_id=entry.id, position=0, name="омлет"))
        session.commit()

    backfill_tracker = AdminBackfillTracker()
    bot = SimpleNamespace(send_message=AsyncMock())
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        chat=SimpleNamespace(id=7002),
        bot=bot,
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args="1")

    await handle_admin_backfill_nutrition(
        message,
        command,
        session_factory,
        nutrition_service=FailingNutritionService(),
        backfill_tracker=backfill_tracker,
        admin_user_ids=(ADMIN_ID,),
    )

    await backfill_tracker.task

    assert message.answer.await_count == 1
    assert message.answer.await_args.args == ("Запускаю backfill nutrition. Лимит: 1.",)
    assert bot.send_message.await_args.args == (7002, "Backfill nutrition завершился с ошибкой: boom")


async def test_admin_backfill_nutrition_rejects_parallel_run() -> None:
    session_factory = create_session_factory()
    backfill_tracker = AdminBackfillTracker()
    running_task = asyncio.create_task(asyncio.sleep(1))
    backfill_tracker.start(requested_by=ADMIN_ID, limit=1, task=running_task)

    bot = SimpleNamespace(send_message=AsyncMock())
    second_message = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_ID, username="admin"),
        chat=SimpleNamespace(id=7003),
        bot=bot,
        answer=AsyncMock(),
    )

    await handle_admin_backfill_nutrition(
        second_message,
        SimpleNamespace(args="1"),
        session_factory,
        nutrition_service=StaticNutritionEstimationService(raw_payload='{"items": []}'),
        backfill_tracker=backfill_tracker,
        admin_user_ids=(ADMIN_ID,),
    )

    second_message.answer.assert_awaited_once()
    assert second_message.answer.await_args.args == ("Backfill nutrition уже выполняется.",)
    running_task.cancel()


async def test_regular_message_saves_metrics_for_allowed_user() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "known_user")
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
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="known_user"),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        nutrition_service=nutrition_service,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_entry = session.query(Entry).one()
        saved_item = session.query(EntryItem).one()
        metric_count = session.query(EntryItemMetric).count()

    assert saved_entry.entry_type == EntryType.FOOD
    assert saved_item.name == "яблоко"
    assert metric_count == 4
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- яблоко: 180 г\n\nКБЖУ по еде:\n- калории: 220.0 ккал\n- белки: 7.6 г\n- жиры: 2.2 г\n- углеводы: 42.8 г",
    )


async def test_regular_message_denies_unallowed_user_before_processing() -> None:
    session_factory = create_session_factory()
    extraction_service = SimpleNamespace(extract=AsyncMock())
    message = SimpleNamespace(
        text="яблоко",
        from_user=SimpleNamespace(id=DENIED_USER_ID, username="denied_user"),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        assert session.query(Entry).count() == 0

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Доступ к боту не разрешён.",)


async def test_recent_returns_latest_entries_for_allowed_user() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "recent_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="recent_user", timezone="Europe/Moscow")
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
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="recent_user"),
        answer=AsyncMock(),
    )

    await handle_recent(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Последние записи:\n- вода: 250 мл\n- яблоко",
    )


async def test_water_button_creates_water_entry_for_allowed_user() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "water_user")
    message = SimpleNamespace(
        text=WATER_250_ML_BUTTON_TEXT,
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="water_user"),
        answer=AsyncMock(),
    )

    await handle_water_250_ml(message, session_factory, admin_user_ids=(ADMIN_ID,))

    with session_factory() as session:
        saved_entry = session.query(Entry).one()
        saved_item = session.query(EntryItem).one()

    assert saved_entry.entry_type == EntryType.WATER
    assert saved_item.name == "water"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Сохранил:\n- вода: 250 мл",)


async def test_photo_message_creates_entries_and_food_metrics_for_allowed_user() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "photo_user")
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
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="photo_user"),
        bot=SimpleNamespace(download=AsyncMock(return_value=BytesIO(b"image-bytes"))),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        nutrition_service=nutrition_service,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_entry = session.query(Entry).one()
        saved_items = session.query(EntryItem).order_by(EntryItem.id).all()
        metric_count = session.query(EntryItemMetric).count()

    assert saved_entry.entry_type == EntryType.FOOD
    assert [(item.name, item.source_type) for item in saved_items] == [
        ("омлет", "extraction_payload"),
        ("тост", "extraction_payload"),
    ]
    assert metric_count == 8
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- омлет\n- тост\n\nКБЖУ по еде:\n- калории: 441.0 ккал\n- белки: 16.2 г\n- жиры: 5.4 г\n- углеводы: 86.6 г",
    )


async def test_handler_rolls_back_food_write_when_nutrition_payload_is_invalid() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "rollback_user")
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
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="rollback_user"),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        nutrition_service=nutrition_service,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        assert session.query(Entry).count() == 0
        assert session.query(EntryItemMetric).count() == 0

    message.answer.assert_awaited_once()
    assert "невалидный structured payload" in message.answer.await_args.args[0]
