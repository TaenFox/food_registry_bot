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
    handle_goal,
    handle_health,
    handle_message,
    handle_recent,
    handle_settings,
    handle_start,
    handle_today,
    handle_toggle_summary_metric,
    handle_water_250_ml,
)
from food_registry_bot.bot.payloads import SummarySettingsCallback
from food_registry_bot.bot.keyboards import WATER_250_ML_BUTTON_TEXT
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
    UserGoalPreference,
    UserSummaryPreference,
)
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
    assert message.answer.await_args.args == ("Использование: <code>/admin_allow TELEGRAM_USER_ID</code>",)


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
            "- <code>/admin_allow TELEGRAM_USER_ID</code>\n"
            "- <code>/admin_deny TELEGRAM_USER_ID</code>\n"
            "- <code>/admin_backfill_nutrition [LIMIT]</code>"
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
            "- <code>/admin_allow TELEGRAM_USER_ID</code>\n"
            "- <code>/admin_deny TELEGRAM_USER_ID</code>\n"
            "- <code>/admin_backfill_nutrition [LIMIT]</code>"
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
        f"<code>/admin_deny {ALLOWED_USER_ID}</code>\n"
        f"- {DENIED_USER_ID} @denied_user [denied]\n"
        f"<code>/admin_allow {DENIED_USER_ID}</code>\n"
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
        f"<code>/admin_allow {LARGE_DENIED_USER_ID}</code>\n"
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
    assert message.answer.await_args.args == ("Использование: <code>/admin_backfill_nutrition [LIMIT]</code>",)


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


async def test_today_returns_daily_nutrition_totals_for_allowed_user() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "today_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="today_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()

        included_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        excluded_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            occurred_at=datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc),
        )
        session.add_all([included_entry, excluded_entry])
        session.flush()

        included_item = EntryItem(entry_id=included_entry.id, position=0, name="омлет")
        excluded_item = EntryItem(entry_id=excluded_entry.id, position=0, name="тост")
        session.add_all([included_item, excluded_item])
        session.flush()
        session.add_all(
            [
                EntryItemMetric(entry_item_id=included_item.id, metric_id=1, value=320.0, confidence="medium"),
                EntryItemMetric(entry_item_id=included_item.id, metric_id=2, value=24.0, confidence="medium"),
                EntryItemMetric(entry_item_id=included_item.id, metric_id=3, value=19.0, confidence="medium"),
                EntryItemMetric(entry_item_id=included_item.id, metric_id=4, value=11.0, confidence="medium"),
                EntryItemMetric(entry_item_id=excluded_item.id, metric_id=1, value=120.0, confidence="medium"),
                EntryItemMetric(entry_item_id=excluded_item.id, metric_id=2, value=4.0, confidence="medium"),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_user"),
        answer=AsyncMock(),
    )

    await handle_today(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "<pre>"
        "К: 320.0 / 1800 ккал\n"
        "Б: 24.0 / 90 г\n"
        "Ж: 19.0 / 60 г\n"
        "У: 11.0 / 210 г"
        "</pre>\n"
        "\n"
        "Есть записей еды без полного набора метрик: 1. Итог дня пока неполный.",
    )


async def test_today_returns_empty_enabled_metrics_message_when_calories_hidden() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "today_hidden_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="today_hidden_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        session.add(entry)
        session.flush()
        item = EntryItem(entry_id=entry.id, position=0, name="омлет")
        session.add(item)
        session.flush()
        session.add_all(
            [
                EntryItemMetric(entry_item_id=item.id, metric_id=1, value=320.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=2, value=24.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=3, value=19.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=4, value=11.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=False,
                    show_protein=False,
                    show_fat=False,
                    show_carbs=False,
                ),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_hidden_user"),
        answer=AsyncMock(),
    )

    await handle_today(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("В summary сейчас нет включённых показателей.",)


async def test_today_returns_bju_lines_when_calories_disabled_but_bju_enabled() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "today_bju_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="today_bju_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        session.add(entry)
        session.flush()
        item = EntryItem(entry_id=entry.id, position=0, name="омлет")
        session.add(item)
        session.flush()
        session.add_all(
            [
                EntryItemMetric(entry_item_id=item.id, metric_id=1, value=320.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=2, value=24.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=3, value=19.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=4, value=11.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=False,
                    show_protein=True,
                    show_fat=True,
                    show_carbs=True,
                ),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_bju_user"),
        answer=AsyncMock(),
    )

    await handle_today(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "<pre>"
        "Б: 24.0 / 90 г\n"
        "Ж: 19.0 / 60 г\n"
        "У: 11.0 / 210 г"
        "</pre>",
    )


async def test_today_shows_calorie_goal_progress_when_snapshot_exists() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "today_goal_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="today_goal_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        session.add(entry)
        session.flush()
        item = EntryItem(entry_id=entry.id, position=0, name="омлет")
        session.add(item)
        session.flush()
        session.add_all(
            [
                EntryItemMetric(entry_item_id=item.id, metric_id=1, value=320.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=2, value=24.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=3, value=19.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=4, value=11.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=True,
                    show_protein=True,
                    show_fat=True,
                    show_carbs=True,
                ),
                UserGoalPreference(
                    user_id=user.id,
                    calorie_goal=1800,
                    protein_goal=90,
                    fat_goal=60,
                    carbs_goal=210,
                ),
                DailyGoalSnapshot(
                    user_id=user.id,
                    summary_date=datetime(2026, 5, 19, tzinfo=timezone.utc).date(),
                    timezone="Europe/Moscow",
                    nutrition_day_start_hour=4,
                    calorie_goal=1800,
                    protein_goal=90,
                    fat_goal=60,
                    carbs_goal=210,
                ),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_goal_user"),
        answer=AsyncMock(),
    )

    await handle_today(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "<pre>"
        "К: 320.0 / 1800 ккал\n"
        "Б: 24.0 / 90 г\n"
        "Ж: 19.0 / 60 г\n"
        "У: 11.0 / 210 г"
        "</pre>",
    )


async def test_today_does_not_show_calorie_goal_progress_when_calories_hidden() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "today_hidden_goal_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="today_hidden_goal_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        session.add(entry)
        session.flush()
        item = EntryItem(entry_id=entry.id, position=0, name="омлет")
        session.add(item)
        session.flush()
        session.add_all(
            [
                EntryItemMetric(entry_item_id=item.id, metric_id=1, value=320.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=2, value=24.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=3, value=19.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=4, value=11.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=False,
                    show_protein=True,
                    show_fat=True,
                    show_carbs=True,
                ),
                UserGoalPreference(
                    user_id=user.id,
                    calorie_goal=1800,
                    protein_goal=90,
                    fat_goal=60,
                    carbs_goal=210,
                ),
                DailyGoalSnapshot(
                    user_id=user.id,
                    summary_date=datetime(2026, 5, 19, tzinfo=timezone.utc).date(),
                    timezone="Europe/Moscow",
                    nutrition_day_start_hour=4,
                    calorie_goal=1800,
                    protein_goal=90,
                    fat_goal=60,
                    carbs_goal=210,
                ),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_hidden_goal_user"),
        answer=AsyncMock(),
    )

    await handle_today(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "<pre>"
        "Б: 24.0 / 90 г\n"
        "Ж: 19.0 / 60 г\n"
        "У: 11.0 / 210 г"
        "</pre>",
    )


async def test_settings_returns_current_summary_preferences() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "settings_user")
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="settings_user"),
        answer=AsyncMock(),
    )

    await handle_settings(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Настройки summary:\n"
        "- калории: включено\n"
        "- белки: включено\n"
        "- жиры: включено\n"
        "- углеводы: включено\n"
        "- отображение: текст\n"
        "- начало дня: 04:00",
    )
    reply_markup = message.answer.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[0][0].text == "Калории: on"
    assert reply_markup.inline_keyboard[1][0].text == "Белки: on"
    assert reply_markup.inline_keyboard[2][0].text == "Жиры: on"
    assert reply_markup.inline_keyboard[3][0].text == "Углеводы: on"
    assert reply_markup.inline_keyboard[4][0].text == "Отображение: текст"
    assert reply_markup.inline_keyboard[5][0].text == "Начало дня: 04:00"


async def test_toggle_summary_metric_updates_preference_and_message() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "settings_toggle_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="settings_toggle_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        session.add(
            UserSummaryPreference(
                user_id=user.id,
                show_calories=True,
                show_protein=True,
                show_fat=True,
                show_carbs=True,
            )
        )
        session.commit()

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="settings_toggle_user"),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await handle_toggle_summary_metric(
        callback,
        SummarySettingsCallback(action="toggle_protein"),
        session_factory,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_preference = session.query(UserSummaryPreference).one()

    assert saved_preference.show_calories is True
    assert saved_preference.show_protein is False
    callback.message.edit_text.assert_awaited_once()
    assert callback.message.edit_text.await_args.args == (
        "Настройки summary:\n"
        "- калории: включено\n"
        "- белки: выключено\n"
        "- жиры: включено\n"
        "- углеводы: включено\n"
        "- отображение: текст\n"
        "- начало дня: 04:00",
    )
    reply_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[0][0].text == "Калории: on"
    assert reply_markup.inline_keyboard[1][0].text == "Белки: off"
    callback.answer.assert_awaited_once_with("Настройка обновлена.")


async def test_cycle_nutrition_day_start_hour_updates_preference_and_message() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "settings_day_start_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="settings_day_start_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        session.add(
            UserSummaryPreference(
                user_id=user.id,
                show_calories=True,
                show_protein=True,
                show_fat=True,
                show_carbs=True,
                nutrition_day_start_hour=4,
            )
        )
        session.commit()

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="settings_day_start_user"),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await handle_toggle_summary_metric(
        callback,
        SummarySettingsCallback(action="cycle_nutrition_day_start_hour"),
        session_factory,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_preference = session.query(UserSummaryPreference).one()

    assert saved_preference.nutrition_day_start_hour == 6
    assert callback.message.edit_text.await_args.args == (
        "Настройки summary:\n"
        "- калории: включено\n"
        "- белки: включено\n"
        "- жиры: включено\n"
        "- углеводы: включено\n"
        "- отображение: текст\n"
        "- начало дня: 06:00",
    )
    reply_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[5][0].text == "Начало дня: 06:00"


async def test_cycle_summary_display_mode_updates_preference_and_message() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "settings_display_mode_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="settings_display_mode_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        session.add(
            UserSummaryPreference(
                user_id=user.id,
                show_calories=True,
                show_protein=True,
                show_fat=True,
                show_carbs=True,
                summary_display_mode="text",
                nutrition_day_start_hour=4,
            )
        )
        session.commit()

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="settings_display_mode_user"),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await handle_toggle_summary_metric(
        callback,
        SummarySettingsCallback(action="cycle_summary_display_mode"),
        session_factory,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_preference = session.query(UserSummaryPreference).one()

    assert saved_preference.summary_display_mode == "bars"
    assert callback.message.edit_text.await_args.args == (
        "Настройки summary:\n"
        "- калории: включено\n"
        "- белки: включено\n"
        "- жиры: включено\n"
        "- углеводы: включено\n"
        "- отображение: бары\n"
        "- начало дня: 04:00",
    )
    reply_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[4][0].text == "Отображение: бары"


async def test_today_uses_preference_nutrition_day_start_hour() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "today_custom_day_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="today_custom_day_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        early_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            occurred_at=datetime(2026, 5, 19, 0, 30, tzinfo=timezone.utc),
        )
        later_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            occurred_at=datetime(2026, 5, 19, 3, 30, tzinfo=timezone.utc),
        )
        session.add_all([early_entry, later_entry])
        session.flush()
        early_item = EntryItem(entry_id=early_entry.id, position=0, name="ночной перекус")
        later_item = EntryItem(entry_id=later_entry.id, position=0, name="поздний завтрак")
        session.add_all([early_item, later_item])
        session.flush()
        session.add_all(
            [
                EntryItemMetric(entry_item_id=early_item.id, metric_id=1, value=150.0, confidence="medium"),
                EntryItemMetric(entry_item_id=early_item.id, metric_id=2, value=10.0, confidence="medium"),
                EntryItemMetric(entry_item_id=early_item.id, metric_id=3, value=5.0, confidence="medium"),
                EntryItemMetric(entry_item_id=early_item.id, metric_id=4, value=12.0, confidence="medium"),
                EntryItemMetric(entry_item_id=later_item.id, metric_id=1, value=300.0, confidence="medium"),
                EntryItemMetric(entry_item_id=later_item.id, metric_id=2, value=20.0, confidence="medium"),
                EntryItemMetric(entry_item_id=later_item.id, metric_id=3, value=8.0, confidence="medium"),
                EntryItemMetric(entry_item_id=later_item.id, metric_id=4, value=25.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=True,
                    show_protein=False,
                    show_fat=False,
                    show_carbs=False,
                    nutrition_day_start_hour=6,
                ),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_custom_day_user"),
        answer=AsyncMock(),
    )

    original_datetime = handle_today.__globals__["datetime"]

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 5, 19, 3, 0, tzinfo=timezone.utc)

    handle_today.__globals__["datetime"] = FixedDateTime
    try:
        await handle_today(message, session_factory, admin_user_ids=(ADMIN_ID,))
    finally:
        handle_today.__globals__["datetime"] = original_datetime

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("<pre>К: 300.0 / 1800 ккал</pre>",)


async def test_today_shows_calorie_progress_bar_in_bars_mode() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "today_bar_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="today_bar_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        session.add(entry)
        session.flush()
        item = EntryItem(entry_id=entry.id, position=0, name="омлет")
        session.add(item)
        session.flush()
        session.add_all(
            [
                EntryItemMetric(entry_item_id=item.id, metric_id=1, value=320.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=2, value=24.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=3, value=19.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=4, value=11.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=True,
                    show_protein=True,
                    show_fat=True,
                    show_carbs=True,
                    summary_display_mode="bars",
                ),
                UserGoalPreference(
                    user_id=user.id,
                    calorie_goal=1800,
                    protein_goal=90,
                    fat_goal=60,
                    carbs_goal=210,
                ),
                DailyGoalSnapshot(
                    user_id=user.id,
                    summary_date=datetime(2026, 5, 19, tzinfo=timezone.utc).date(),
                    timezone="Europe/Moscow",
                    nutrition_day_start_hour=4,
                    calorie_goal=1800,
                    protein_goal=90,
                    fat_goal=60,
                    carbs_goal=210,
                ),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_bar_user"),
        answer=AsyncMock(),
    )

    await handle_today(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "<pre>"
        "К [█░░░░░░░░░] 17.8% 320.0/1800 ккал\n"
        "Б [██░░░░░░░░] 26.7% 24.0/90 г\n"
        "Ж [███░░░░░░░] 31.7% 19.0/60 г\n"
        "У [░░░░░░░░░░] 5.2% 11.0/210 г"
        "</pre>",
    )


async def test_goal_returns_default_goals_when_preference_is_not_created() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "goal_user")
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="goal_user"),
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args=None)

    original_datetime = handle_goal.__globals__["datetime"]

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)

    handle_goal.__globals__["datetime"] = FixedDateTime
    try:
        await handle_goal(message, command, session_factory, admin_user_ids=(ADMIN_ID,))
    finally:
        handle_goal.__globals__["datetime"] = original_datetime

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Текущие цели:\n"
        "- калории: 1800 ккал\n"
        "- белки: 90 г\n"
        "- жиры: 60 г\n"
        "- углеводы: 210 г\n"
        "\n"
        "Настройка:\n"
        "- <code>/goal 1800</code>\n"
        "- <code>/goal protein 90</code>\n"
        "- <code>/goal fat 60</code>\n"
        "- <code>/goal carbs 210</code>\n"
        "\n"
        "Пищевой день 2026-05-19:\n"
        "- калории: 1800 ккал\n"
        "- белки: 90 г\n"
        "- жиры: 60 г\n"
        "- углеводы: 210 г\n"
        "Часовой пояс дня: Europe/Moscow.\n"
        "Начало пищевого дня: 04:00.",
    )


async def test_goal_sets_preference_and_creates_snapshot_for_current_nutrition_day() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "goal_user")
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="goal_user"),
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args="1800")

    original_datetime = handle_goal.__globals__["datetime"]

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)

    handle_goal.__globals__["datetime"] = FixedDateTime
    try:
        await handle_goal(message, command, session_factory, admin_user_ids=(ADMIN_ID,))
    finally:
        handle_goal.__globals__["datetime"] = original_datetime

    with session_factory() as session:
        saved_goal = session.query(UserGoalPreference).one()
        saved_snapshot = session.query(DailyGoalSnapshot).one()

    assert saved_goal.calorie_goal == 1800
    assert saved_goal.protein_goal == 90
    assert saved_snapshot.summary_date.isoformat() == "2026-05-19"
    assert saved_snapshot.calorie_goal == 1800
    assert saved_snapshot.protein_goal == 90
    assert saved_snapshot.timezone == "Europe/Moscow"
    assert saved_snapshot.nutrition_day_start_hour == 4
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Текущие цели:\n"
        "- калории: 1800 ккал\n"
        "- белки: 90 г\n"
        "- жиры: 60 г\n"
        "- углеводы: 210 г\n"
        "\n"
        "Настройка:\n"
        "- <code>/goal 1800</code>\n"
        "- <code>/goal protein 90</code>\n"
        "- <code>/goal fat 60</code>\n"
        "- <code>/goal carbs 210</code>\n"
        "\n"
        "Пищевой день 2026-05-19:\n"
        "- калории: 1800 ккал\n"
        "- белки: 90 г\n"
        "- жиры: 60 г\n"
        "- углеводы: 210 г\n"
        "Часовой пояс дня: Europe/Moscow.\n"
        "Начало пищевого дня: 04:00.",
    )


async def test_goal_sets_macro_goal_by_metric_code() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "goal_macro_user")
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="goal_macro_user"),
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args="protein 110")

    original_datetime = handle_goal.__globals__["datetime"]

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)

    handle_goal.__globals__["datetime"] = FixedDateTime
    try:
        await handle_goal(message, command, session_factory, admin_user_ids=(ADMIN_ID,))
    finally:
        handle_goal.__globals__["datetime"] = original_datetime

    with session_factory() as session:
        saved_goal = session.query(UserGoalPreference).one()
        saved_snapshot = session.query(DailyGoalSnapshot).one()

    assert saved_goal.protein_goal == 110
    assert saved_snapshot.protein_goal == 110
    message.answer.assert_awaited_once()
    assert "белки: 110 г" in message.answer.await_args.args[0]


async def test_goal_hint_respects_enabled_summary_metrics() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "goal_hint_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="goal_hint_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        session.add(
            UserSummaryPreference(
                user_id=user.id,
                show_calories=False,
                show_protein=True,
                show_fat=False,
                show_carbs=True,
            )
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="goal_hint_user"),
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args=None)

    original_datetime = handle_goal.__globals__["datetime"]

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)

    handle_goal.__globals__["datetime"] = FixedDateTime
    try:
        await handle_goal(message, command, session_factory, admin_user_ids=(ADMIN_ID,))
    finally:
        handle_goal.__globals__["datetime"] = original_datetime

    rendered = message.answer.await_args.args[0]
    assert "Настройка:\n- <code>/goal protein 90</code>\n- <code>/goal carbs 210</code>\n" in rendered
    assert "<code>/goal 1800</code>" not in rendered
    assert "<code>/goal fat 60</code>" not in rendered


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
