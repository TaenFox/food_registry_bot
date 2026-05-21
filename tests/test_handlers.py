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
    build_ambiguous_message_response,
    handle_admin,
    handle_admin_allow,
    handle_admin_backfill_nutrition,
    handle_admin_deny,
    handle_admin_users,
    handle_goal,
    handle_health,
    handle_message,
    handle_recent,
    handle_recent_delete_callback,
    handle_settings,
    handle_start,
    handle_today,
    handle_toggle_summary_metric,
    handle_water_250_ml,
)
from food_registry_bot.bot.message_routing import MessageRoutingDecision
from food_registry_bot.bot.payloads import RecentEntryDeleteCallback, SummarySettingsCallback
from food_registry_bot.bot.keyboards import WATER_250_ML_BUTTON_TEXT
from food_registry_bot.db.base import Base
from food_registry_bot.db.models import (
    ConversationMessageRole,
    ConversationMessage,
    ConversationSession,
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
    ExtractedJournalMetric,
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
                SupportedMetric(code="fiber", name="Fiber", unit="g"),
                SupportedMetric(code="workout_calories", name="Workout Calories", unit="kcal"),
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
                    {"code": "fiber", "value": 5.1 + index, "confidence": confidence},
                ],
            }
        )
    return json.dumps({"items": items}, ensure_ascii=False)


async def call_handle_today_at(
    *,
    fixed_now: datetime,
    message,
    session_factory: sessionmaker[Session],
) -> None:
    original_datetime = handle_today.__globals__["datetime"]

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return fixed_now

    handle_today.__globals__["datetime"] = FixedDateTime
    try:
        await handle_today(message, session_factory, admin_user_ids=(ADMIN_ID,))
    finally:
        handle_today.__globals__["datetime"] = original_datetime


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
    assert message.answer.await_args.args == (
        "Сейчас у тебя нет доступа к боту. Если он нужен, попроси администратора добавить твой Telegram ID.",
    )


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
    assert message.answer.await_args.args == (
        "Профиль создан.\n"
        "Что можно сделать:\n"
        "- отправить запись еды текстом или фото блюда;\n"
        "- нажать кнопку воды;\n"
        "- при желании включить запись тренировок в /settings;\n"
        "- задать вопрос о питании;\n"
        "- посмотреть итог дня: /today;\n"
        "- посмотреть и удалить последние записи: /recent;\n"
        "- посмотреть или изменить цели: /goal;\n"
        "- настроить summary: /settings.",
    )


async def test_health_denies_unallowed_user() -> None:
    session_factory = create_session_factory()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=DENIED_USER_ID, username="denied_user"),
        answer=AsyncMock(),
    )

    await handle_health(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сейчас у тебя нет доступа к боту. Если он нужен, попроси администратора добавить твой Telegram ID.",
    )


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

    await handle_admin(
        message,
        session_factory,
        backfill_tracker=AdminBackfillTracker(),
        admin_user_ids=(ADMIN_ID,),
        app_version="v1",
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        (
            "Панель администратора:\n"
            "- версия бота: v1\n"
            f"- текущий админ: {ADMIN_ID}\n"
            "- админов в конфиге: 1\n"
            "- известных пользователей: 2\n"
            "- разрешённых пользователей: 1\n"
            "- запрещённых пользователей: 1\n"
            "- пользователей с профилем: 1\n"
            "- food entries без полного набора метрик: 1\n"
            "- дозаполнение nutrition metrics: idle\n"
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

    await handle_admin(
        message,
        session_factory,
        backfill_tracker=AdminBackfillTracker(),
        admin_user_ids=(ADMIN_ID,),
        app_version="v1",
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        (
            "Панель администратора:\n"
            "- версия бота: v1\n"
            f"- текущий админ: {ADMIN_ID}\n"
            "- админов в конфиге: 1\n"
            "- известных пользователей: 1\n"
            "- разрешённых пользователей: 0\n"
            "- запрещённых пользователей: 1\n"
            "- пользователей с профилем: 0\n"
            "- food entries без полного набора метрик: 0\n"
            "- дозаполнение nutrition metrics: idle\n"
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
        f"- {ALLOWED_USER_ID} @allowed_user [доступ разрешён]\n"
        f"<code>/admin_deny {ALLOWED_USER_ID}</code>\n"
        f"- {DENIED_USER_ID} @denied_user [доступ запрещён]\n"
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
        f"- {LARGE_DENIED_USER_ID} @new_user [доступ запрещён]\n"
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
        assert session.query(EntryItemMetric).count() == 5

    assert message.answer.await_count == 1
    assert message.answer.await_args.args == ("Запускаю дозаполнение nutrition metrics. Лимит: 20.",)
    assert bot.send_message.await_args.args == (
        7001,
        "Дозаполнение nutrition metrics завершено.\n"
        "Выбрано записей: 1\n"
        "Обработано записей: 1\n"
        "Сохранено метрик: 5",
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
    assert message.answer.await_args.args == ("Запускаю дозаполнение nutrition metrics. Лимит: 1.",)
    assert bot.send_message.await_args.args == (7002, "Дозаполнение nutrition metrics завершилось с ошибкой: boom")


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
    assert second_message.answer.await_args.args == ("Дозаполнение nutrition metrics уже выполняется.",)
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
    assert metric_count == 5
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- яблоко (180 г)\n\n"
        "<pre>"
        "К: 220.0 / 1800 ккал (+220.0 ккал)\n"
        "Б: 7.6 / 90 г (+7.6 г)\n"
        "Ж: 2.2 / 60 г (+2.2 г)\n"
        "У: 42.8 / 210 г (+42.8 г)\n"
        "Кл: 5.1 / 25 г (+5.1 г)\n"
        "В: 0.0 / 2000 мл"
        "</pre>",
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
    assert message.answer.await_args.args == (
        "Сейчас у тебя нет доступа к боту. Если он нужен, попроси администратора добавить твой Telegram ID.",
    )


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
        "Последние записи:\n1. 14:00 — вода (250 мл)\n2. 13:00 — яблоко",
    )
    reply_markup = message.answer.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[0][0].text == "Выбрать для удаления"


async def test_recent_delete_open_shows_entry_selection_buttons() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "recent_delete_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="recent_delete_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()

        first_entry = Entry(user_id=user.id, entry_type=EntryType.FOOD, source_text="яблоко", occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc))
        second_entry = Entry(user_id=user.id, entry_type=EntryType.WATER, source_text="250 мл", occurred_at=datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc))
        session.add_all([first_entry, second_entry])
        session.flush()

        session.add(EntryItem(entry_id=first_entry.id, position=0, name="яблоко"))
        session.add(EntryItem(entry_id=second_entry.id, position=0, name="water", quantity=250, unit="ml"))
        session.commit()

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="recent_delete_user"),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
        answer=AsyncMock(),
    )

    await handle_recent_delete_callback(
        callback,
        RecentEntryDeleteCallback(action="open", entry_id=0),
        session_factory,
        admin_user_ids=(ADMIN_ID,),
    )

    callback.message.edit_text.assert_awaited_once()
    assert callback.message.edit_text.await_args.args == (
        "Последние записи:\n"
        "1. 14:00 — вода (250 мл)\n"
        "2. 13:00 — яблоко\n"
        "\n"
        "Выбери запись, которую нужно удалить.",
    )
    reply_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[0][0].text == "14:00 · вода (250 мл)"
    assert reply_markup.inline_keyboard[1][0].text == "13:00 · яблоко"
    assert reply_markup.inline_keyboard[2][0].text == "Отмена"
    callback.answer.assert_awaited_once_with()


async def test_recent_delete_open_returns_selection_screen() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "recent_delete_cancel_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="recent_delete_cancel_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()

        entry = Entry(user_id=user.id, entry_type=EntryType.FOOD, source_text="яблоко", occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc))
        session.add(entry)
        session.flush()
        session.add(EntryItem(entry_id=entry.id, position=0, name="яблоко"))
        session.commit()

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="recent_delete_cancel_user"),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
        answer=AsyncMock(),
    )

    await handle_recent_delete_callback(
        callback,
        RecentEntryDeleteCallback(action="open", entry_id=0),
        session_factory,
        admin_user_ids=(ADMIN_ID,),
    )

    assert callback.message.edit_text.await_args.args == (
        "Последние записи:\n"
        "1. 13:00 — яблоко\n"
        "\n"
        "Выбери запись, которую нужно удалить.",
    )


async def test_recent_delete_confirm_removes_entry_and_refreshes_recent_list() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "recent_delete_confirm_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="recent_delete_confirm_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        entry = Entry(
            user_id=user.id,
            entry_type=EntryType.FOOD,
            source_text="омлет",
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        later_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.WATER,
            source_text="250 мл",
            occurred_at=datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc),
        )
        session.add_all([entry, later_entry])
        session.flush()
        item = EntryItem(entry_id=entry.id, position=0, name="омлет")
        later_item = EntryItem(entry_id=later_entry.id, position=0, name="water", quantity=250, unit="ml")
        session.add_all([item, later_item])
        session.flush()
        session.add_all(
            [
                EntryItemMetric(entry_item_id=item.id, metric_id=1, value=320.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=2, value=24.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=3, value=19.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=4, value=11.0, confidence="medium"),
                EntryItemMetric(entry_item_id=item.id, metric_id=5, value=6.0, confidence="medium"),
            ]
        )
        session.commit()
        entry_id = entry.id

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="recent_delete_confirm_user"),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
        answer=AsyncMock(),
    )

    await handle_recent_delete_callback(
        callback,
        RecentEntryDeleteCallback(action="confirm", entry_id=entry_id),
        session_factory,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        assert session.query(Entry).count() == 1

    callback.message.edit_text.assert_awaited_once_with(
        "Последние записи:\n1. 12:00 — вода (250 мл)",
        reply_markup=callback.message.edit_text.await_args.kwargs["reply_markup"],
    )
    reply_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[0][0].text == "Выбрать для удаления"
    callback.message.answer.assert_not_awaited()
    callback.answer.assert_awaited_once_with("Запись удалена. Список уже обновлён.")


async def test_recent_delete_returns_safe_error_for_stale_button() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "recent_delete_stale_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="recent_delete_stale_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()

        entry = Entry(user_id=user.id, entry_type=EntryType.FOOD, source_text="яблоко", occurred_at=datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc))
        session.add(entry)
        session.flush()
        session.add(EntryItem(entry_id=entry.id, position=0, name="яблоко"))
        session.commit()
        entry_id = entry.id

    with session_factory() as session:
        session.delete(session.query(Entry).filter_by(id=entry_id).one())
        session.commit()

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="recent_delete_stale_user"),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()),
        answer=AsyncMock(),
    )

    await handle_recent_delete_callback(
        callback,
        RecentEntryDeleteCallback(action="confirm", entry_id=entry_id),
        session_factory,
        admin_user_ids=(ADMIN_ID,),
    )

    callback.message.edit_text.assert_not_awaited()
    callback.answer.assert_awaited_once_with("Эта запись уже удалена или больше недоступна.", show_alert=True)


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
                EntryItemMetric(entry_item_id=included_item.id, metric_id=5, value=6.0, confidence="medium"),
                EntryItemMetric(entry_item_id=excluded_item.id, metric_id=1, value=120.0, confidence="medium"),
                EntryItemMetric(entry_item_id=excluded_item.id, metric_id=2, value=4.0, confidence="medium"),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_user"),
        answer=AsyncMock(),
    )

    await call_handle_today_at(
        fixed_now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        message=message,
        session_factory=session_factory,
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "<pre>"
        "К: 320.0 / 1800 ккал\n"
        "Б: 24.0 / 90 г\n"
        "Ж: 19.0 / 60 г\n"
        "У: 11.0 / 210 г\n"
        "Кл: 6.0 / 25 г\n"
        "В: 0.0 / 2000 мл"
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
                EntryItemMetric(entry_item_id=item.id, metric_id=5, value=6.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=False,
                    show_protein=False,
                    show_fat=False,
                    show_carbs=False,
                    show_fiber=False,
                    show_water=False,
                ),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_hidden_user"),
        answer=AsyncMock(),
    )

    await call_handle_today_at(
        fixed_now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        message=message,
        session_factory=session_factory,
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("В summary сейчас всё скрыто. Включи хотя бы один показатель в /settings.",)


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
                EntryItemMetric(entry_item_id=item.id, metric_id=5, value=6.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=False,
                    show_protein=True,
                    show_fat=True,
                    show_carbs=True,
                    show_fiber=False,
                    show_water=False,
                ),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_bju_user"),
        answer=AsyncMock(),
    )

    await call_handle_today_at(
        fixed_now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        message=message,
        session_factory=session_factory,
    )

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
                EntryItemMetric(entry_item_id=item.id, metric_id=5, value=6.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=True,
                    show_protein=True,
                    show_fat=True,
                    show_carbs=True,
                    show_fiber=False,
                    show_water=False,
                ),
                UserGoalPreference(
                    user_id=user.id,
                    calorie_goal=1800,
                    protein_goal=90,
                    fat_goal=60,
                    carbs_goal=210,
                    fiber_goal=25,
                    water_goal=2000,
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
                    fiber_goal=25,
                    water_goal=2000,
                ),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_goal_user"),
        answer=AsyncMock(),
    )

    await call_handle_today_at(
        fixed_now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        message=message,
        session_factory=session_factory,
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "<pre>"
        "К: 320.0 / 1800 ккал\n"
        "Б: 24.0 / 90 г\n"
        "Ж: 19.0 / 60 г\n"
        "У: 11.0 / 210 г"
        "</pre>",
    )


async def test_today_shows_water_progress_for_water_entries() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "today_water_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="today_water_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        water_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.WATER,
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        session.add(water_entry)
        session.flush()
        session.add(EntryItem(entry_id=water_entry.id, position=0, name="water", quantity=500, unit="ml"))
        session.add(
            UserSummaryPreference(
                user_id=user.id,
                show_calories=False,
                show_protein=False,
                show_fat=False,
                show_carbs=False,
                show_fiber=False,
                show_water=True,
            )
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_water_user"),
        answer=AsyncMock(),
    )

    await call_handle_today_at(
        fixed_now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        message=message,
        session_factory=session_factory,
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("<pre>В: 500.0 / 2000 мл</pre>",)


async def test_today_shows_workout_list_only_when_workout_logging_is_enabled() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "today_workout_user")
    with session_factory() as session:
        user = User(
            telegram_user_id=ALLOWED_USER_ID,
            username="today_workout_user",
            timezone="Europe/Moscow",
        )
        user.workout_logging_enabled = True
        session.add(user)
        session.flush()
        workout_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.WORKOUT,
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        session.add(workout_entry)
        session.flush()
        session.add(EntryItem(entry_id=workout_entry.id, position=0, name="бег", quantity=40, unit="min"))
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_workout_user"),
        answer=AsyncMock(),
    )

    await call_handle_today_at(
        fixed_now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        message=message,
        session_factory=session_factory,
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Тренировки:\n- 11:00 — бег (40 мин)",)


async def test_today_hides_workout_list_when_workout_logging_is_disabled() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "today_workout_hidden_user")
    with session_factory() as session:
        user = User(
            telegram_user_id=ALLOWED_USER_ID,
            username="today_workout_hidden_user",
            timezone="Europe/Moscow",
        )
        user.workout_logging_enabled = False
        session.add(user)
        session.flush()
        workout_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.WORKOUT,
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        session.add(workout_entry)
        session.flush()
        session.add(EntryItem(entry_id=workout_entry.id, position=0, name="бег", quantity=40, unit="min"))
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_workout_hidden_user"),
        answer=AsyncMock(),
    )

    await call_handle_today_at(
        fixed_now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        message=message,
        session_factory=session_factory,
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("За текущий день пока нет записей. Отправь еду, фото блюда или воду.",)


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
                EntryItemMetric(entry_item_id=item.id, metric_id=5, value=6.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=False,
                    show_protein=True,
                    show_fat=True,
                    show_carbs=True,
                    show_fiber=False,
                    show_water=False,
                ),
                UserGoalPreference(
                    user_id=user.id,
                    calorie_goal=1800,
                    protein_goal=90,
                    fat_goal=60,
                    carbs_goal=210,
                    fiber_goal=25,
                    water_goal=2000,
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
                    fiber_goal=25,
                    water_goal=2000,
                ),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_hidden_goal_user"),
        answer=AsyncMock(),
    )

    await call_handle_today_at(
        fixed_now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        message=message,
        session_factory=session_factory,
    )

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
        "- тренировки: выключено\n"
        "- калории: включено\n"
        "- белки: включено\n"
        "- жиры: включено\n"
        "- углеводы: включено\n"
        "- клетчатка: включено\n"
        "- вода: включено\n"
        "- дельта записи: включено\n"
        "- отображение: текст\n"
        "- начало дня: 04:00",
    )
    reply_markup = message.answer.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[0][0].text == "Тренировки: off"
    assert reply_markup.inline_keyboard[1][0].text == "Калории: on"
    assert reply_markup.inline_keyboard[2][0].text == "Белки: on"
    assert reply_markup.inline_keyboard[3][0].text == "Жиры: on"
    assert reply_markup.inline_keyboard[4][0].text == "Углеводы: on"
    assert reply_markup.inline_keyboard[5][0].text == "Клетчатка: on"
    assert reply_markup.inline_keyboard[6][0].text == "Вода: on"
    assert reply_markup.inline_keyboard[7][0].text == "Дельта записи: on"
    assert reply_markup.inline_keyboard[8][0].text == "Отображение: текст"
    assert reply_markup.inline_keyboard[9][0].text == "Начало дня: 04:00"


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
                show_fiber=True,
                show_water=True,
                show_post_entry_delta_suffix=True,
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
        "- тренировки: выключено\n"
        "- калории: включено\n"
        "- белки: выключено\n"
        "- жиры: включено\n"
        "- углеводы: включено\n"
        "- клетчатка: включено\n"
        "- вода: включено\n"
        "- дельта записи: включено\n"
        "- отображение: текст\n"
        "- начало дня: 04:00",
    )
    reply_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[1][0].text == "Калории: on"
    assert reply_markup.inline_keyboard[2][0].text == "Белки: off"
    callback.answer.assert_awaited_once_with("Сохранил настройки.")


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
                show_fiber=True,
                show_water=True,
                show_post_entry_delta_suffix=True,
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
        "- тренировки: выключено\n"
        "- калории: включено\n"
        "- белки: включено\n"
        "- жиры: включено\n"
        "- углеводы: включено\n"
        "- клетчатка: включено\n"
        "- вода: включено\n"
        "- дельта записи: включено\n"
        "- отображение: текст\n"
        "- начало дня: 06:00",
    )
    reply_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[9][0].text == "Начало дня: 06:00"


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
                show_fiber=True,
                show_water=True,
                show_post_entry_delta_suffix=True,
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
        "- тренировки: выключено\n"
        "- калории: включено\n"
        "- белки: включено\n"
        "- жиры: включено\n"
        "- углеводы: включено\n"
        "- клетчатка: включено\n"
        "- вода: включено\n"
        "- дельта записи: включено\n"
        "- отображение: бары\n"
        "- начало дня: 04:00",
    )
    reply_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[8][0].text == "Отображение: бары"


async def test_toggle_post_entry_delta_suffix_updates_preference_and_message() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "settings_delta_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="settings_delta_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        session.add(
            UserSummaryPreference(
                user_id=user.id,
                show_calories=True,
                show_protein=True,
                show_fat=True,
                show_carbs=True,
                show_fiber=True,
                show_water=True,
                show_post_entry_delta_suffix=True,
            )
        )
        session.commit()

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="settings_delta_user"),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await handle_toggle_summary_metric(
        callback,
        SummarySettingsCallback(action="toggle_post_entry_delta_suffix"),
        session_factory,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_preference = session.query(UserSummaryPreference).one()

    assert saved_preference.show_post_entry_delta_suffix is False
    assert callback.message.edit_text.await_args.args == (
        "Настройки summary:\n"
        "- тренировки: выключено\n"
        "- калории: включено\n"
        "- белки: включено\n"
        "- жиры: включено\n"
        "- углеводы: включено\n"
        "- клетчатка: включено\n"
        "- вода: включено\n"
        "- дельта записи: выключено\n"
        "- отображение: текст\n"
        "- начало дня: 04:00",
    )
    reply_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[7][0].text == "Дельта записи: off"
    callback.answer.assert_awaited_once_with("Сохранил настройки.")


async def test_toggle_workout_logging_updates_user_and_message() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "settings_workout_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="settings_workout_user", timezone="Europe/Moscow")
        session.add(user)
        session.commit()

    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="settings_workout_user"),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )

    await handle_toggle_summary_metric(
        callback,
        SummarySettingsCallback(action="toggle_workout_logging"),
        session_factory,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_user = session.query(User).one()

    assert saved_user.workout_logging_enabled is True
    assert callback.message.edit_text.await_args.args == (
        "Настройки summary:\n"
        "- тренировки: включено\n"
        "- калории: включено\n"
        "- белки: включено\n"
        "- жиры: включено\n"
        "- углеводы: включено\n"
        "- клетчатка: включено\n"
        "- вода: включено\n"
        "- дельта записи: включено\n"
        "- отображение: текст\n"
        "- начало дня: 04:00",
    )
    reply_markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert reply_markup.inline_keyboard[0][0].text == "Тренировки: on"
    callback.answer.assert_awaited_once_with("Сохранил настройки.")


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
                EntryItemMetric(entry_item_id=early_item.id, metric_id=5, value=3.0, confidence="medium"),
                EntryItemMetric(entry_item_id=later_item.id, metric_id=1, value=300.0, confidence="medium"),
                EntryItemMetric(entry_item_id=later_item.id, metric_id=2, value=20.0, confidence="medium"),
                EntryItemMetric(entry_item_id=later_item.id, metric_id=3, value=8.0, confidence="medium"),
                EntryItemMetric(entry_item_id=later_item.id, metric_id=4, value=25.0, confidence="medium"),
                EntryItemMetric(entry_item_id=later_item.id, metric_id=5, value=7.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=True,
                    show_protein=False,
                    show_fat=False,
                    show_carbs=False,
                    show_fiber=False,
                    show_water=False,
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
                EntryItemMetric(entry_item_id=item.id, metric_id=5, value=6.0, confidence="medium"),
                UserSummaryPreference(
                    user_id=user.id,
                    show_calories=True,
                    show_protein=True,
                    show_fat=True,
                    show_carbs=True,
                    show_fiber=False,
                    show_water=False,
                    summary_display_mode="bars",
                ),
                UserGoalPreference(
                    user_id=user.id,
                    calorie_goal=1800,
                    protein_goal=90,
                    fat_goal=60,
                    carbs_goal=210,
                    fiber_goal=25,
                    water_goal=2000,
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
                    fiber_goal=25,
                    water_goal=2000,
                ),
            ]
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_bar_user"),
        answer=AsyncMock(),
    )

    await call_handle_today_at(
        fixed_now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        message=message,
        session_factory=session_factory,
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "<pre>"
        "Ккал   [█░░░░░░░░░] 17.8% 320.0/1800 ккал\n"
        "Б      [██░░░░░░░░] 26.7% 24.0/90 г\n"
        "Ж      [███░░░░░░░] 31.7% 19.0/60 г\n"
        "У      [░░░░░░░░░░] 5.2% 11.0/210 г"
        "</pre>",
    )


async def test_today_shows_water_bar_in_bars_mode() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "today_water_bar_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="today_water_bar_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        water_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.WATER,
            occurred_at=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
        )
        session.add(water_entry)
        session.flush()
        session.add(EntryItem(entry_id=water_entry.id, position=0, name="water", quantity=500, unit="ml"))
        session.add(
            UserSummaryPreference(
                user_id=user.id,
                show_calories=False,
                show_protein=False,
                show_fat=False,
                show_carbs=False,
                show_fiber=False,
                show_water=True,
                summary_display_mode="bars",
            )
        )
        session.commit()

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="today_water_bar_user"),
        answer=AsyncMock(),
    )

    await call_handle_today_at(
        fixed_now=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        message=message,
        session_factory=session_factory,
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("<pre>В      [██░░░░░░░░] 25.0% 500.0/2000 мл</pre>",)


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
        "- клетчатка: 25 г\n"
        "- вода: 2000 мл\n"
        "\n"
        "Настройка:\n"
        "- <code>/goal 1800</code>\n"
        "- <code>/goal protein 90</code>\n"
        "- <code>/goal fat 60</code>\n"
        "- <code>/goal carbs 210</code>\n"
        "- <code>/goal fiber 25</code>\n"
        "- <code>/goal water 2000</code>\n"
        "\n"
        "Пищевой день 2026-05-19:\n"
        "- калории: 1800 ккал\n"
        "- белки: 90 г\n"
        "- жиры: 60 г\n"
        "- углеводы: 210 г\n"
        "- клетчатка: 25 г\n"
        "- вода: 2000 мл\n"
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
    assert saved_goal.fiber_goal == 25
    assert saved_goal.water_goal == 2000
    assert saved_snapshot.summary_date.isoformat() == "2026-05-19"
    assert saved_snapshot.calorie_goal == 1800
    assert saved_snapshot.protein_goal == 90
    assert saved_snapshot.fiber_goal == 25
    assert saved_snapshot.water_goal == 2000
    assert saved_snapshot.timezone == "Europe/Moscow"
    assert saved_snapshot.nutrition_day_start_hour == 4
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Текущие цели:\n"
        "- калории: 1800 ккал\n"
        "- белки: 90 г\n"
        "- жиры: 60 г\n"
        "- углеводы: 210 г\n"
        "- клетчатка: 25 г\n"
        "- вода: 2000 мл\n"
        "\n"
        "Настройка:\n"
        "- <code>/goal 1800</code>\n"
        "- <code>/goal protein 90</code>\n"
        "- <code>/goal fat 60</code>\n"
        "- <code>/goal carbs 210</code>\n"
        "- <code>/goal fiber 25</code>\n"
        "- <code>/goal water 2000</code>\n"
        "\n"
        "Пищевой день 2026-05-19:\n"
        "- калории: 1800 ккал\n"
        "- белки: 90 г\n"
        "- жиры: 60 г\n"
        "- углеводы: 210 г\n"
        "- клетчатка: 25 г\n"
        "- вода: 2000 мл\n"
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
                show_fiber=False,
                show_water=False,
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
    assert "<code>/goal water 2000</code>" not in rendered


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
    assert message.answer.await_args.args == (
        "Сохранил:\n- вода (250 мл)\n\n"
        "<pre>"
        "К: 0.0 / 1800 ккал\n"
        "Б: 0.0 / 90 г\n"
        "Ж: 0.0 / 60 г\n"
        "У: 0.0 / 210 г\n"
        "Кл: 0.0 / 25 г\n"
        "В: 250.0 / 2000 мл (+250.0 мл)"
        "</pre>",
    )


async def test_water_button_shows_delta_bar_report_in_bars_mode() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "water_bar_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="water_bar_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        session.add(
            UserSummaryPreference(
                user_id=user.id,
                show_calories=False,
                show_protein=False,
                show_fat=False,
                show_carbs=False,
                show_fiber=False,
                show_water=True,
                summary_display_mode="bars",
            )
        )
        water_entry = Entry(
            user_id=user.id,
            entry_type=EntryType.WATER,
            occurred_at=datetime.now(timezone.utc),
        )
        session.add(water_entry)
        session.flush()
        session.add(EntryItem(entry_id=water_entry.id, position=0, name="water", quantity=500, unit="ml"))
        session.commit()

    message = SimpleNamespace(
        text=WATER_250_ML_BUTTON_TEXT,
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="water_bar_user"),
        answer=AsyncMock(),
    )

    await handle_water_250_ml(message, session_factory, admin_user_ids=(ADMIN_ID,))

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- вода (250 мл)\n\n<pre>В      [██▓░░░░░░░] 37.5% 750.0/2000 мл (+250.0 мл)</pre>",
    )


async def test_confirmation_hides_delta_suffix_when_setting_is_disabled() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "delta_off_user")
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
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="delta_off_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        session.add(
            UserSummaryPreference(
                user_id=user.id,
                show_calories=True,
                show_protein=True,
                show_fat=True,
                show_carbs=True,
                show_fiber=True,
                show_water=True,
                show_post_entry_delta_suffix=False,
            )
        )
        session.commit()

    message = SimpleNamespace(
        text="яблоко",
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="delta_off_user"),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        nutrition_service=nutrition_service,
        admin_user_ids=(ADMIN_ID,),
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- яблоко (180 г)\n\n"
        "<pre>"
        "К: 220.0 / 1800 ккал\n"
        "Б: 7.6 / 90 г\n"
        "Ж: 2.2 / 60 г\n"
        "У: 42.8 / 210 г\n"
        "Кл: 5.1 / 25 г\n"
        "В: 0.0 / 2000 мл"
        "</pre>",
    )


async def test_food_confirmation_appends_post_entry_nutrition_comment() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "post_entry_comment_user")
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
    conversation_service = SimpleNamespace(
        comment_on_food_write=lambda **kwargs: (
            "После этой записи углеводы и клетчатка подросли, но по белку у тебя ещё заметный запас."
            if kwargs["saved_items"] == ["яблоко (180 г)"]
            else None
        )
    )
    message = SimpleNamespace(
        text="яблоко",
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="post_entry_comment_user"),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        nutrition_service=nutrition_service,
        conversation_service=conversation_service,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_entry = session.query(Entry).one()

    assert saved_entry.llm_comment == (
        "После этой записи углеводы и клетчатка подросли, но по белку у тебя ещё заметный запас."
    )
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- яблоко (180 г)\n\n"
        "<pre>"
        "К: 220.0 / 1800 ккал (+220.0 ккал)\n"
        "Б: 7.6 / 90 г (+7.6 г)\n"
        "Ж: 2.2 / 60 г (+2.2 г)\n"
        "У: 42.8 / 210 г (+42.8 г)\n"
        "Кл: 5.1 / 25 г (+5.1 г)\n"
        "В: 0.0 / 2000 мл"
        "</pre>\n\n"
        "Нутрициолог: После этой записи углеводы и клетчатка подросли, но по белку у тебя ещё заметный запас.",
    )


async def test_food_confirmation_still_succeeds_when_post_entry_comment_fails() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "post_entry_comment_error_user")
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
    conversation_service = SimpleNamespace(
        comment_on_food_write=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("comment failed"))
    )
    message = SimpleNamespace(
        text="яблоко",
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="post_entry_comment_error_user"),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        nutrition_service=nutrition_service,
        conversation_service=conversation_service,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_entry = session.query(Entry).one()

    assert saved_entry.llm_comment is None
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- яблоко (180 г)\n\n"
        "<pre>"
        "К: 220.0 / 1800 ккал (+220.0 ккал)\n"
        "Б: 7.6 / 90 г (+7.6 г)\n"
        "Ж: 2.2 / 60 г (+2.2 г)\n"
        "У: 42.8 / 210 г (+42.8 г)\n"
        "Кл: 5.1 / 25 г (+5.1 г)\n"
        "В: 0.0 / 2000 мл"
        "</pre>",
    )


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
    assert metric_count == 10
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- омлет\n- тост\n\n"
        "<pre>"
        "К: 441.0 / 1800 ккал (+441.0 ккал)\n"
        "Б: 16.2 / 90 г (+16.2 г)\n"
        "Ж: 5.4 / 60 г (+5.4 г)\n"
        "У: 86.6 / 210 г (+86.6 г)\n"
        "Кл: 11.2 / 25 г (+11.2 г)\n"
        "В: 0.0 / 2000 мл"
        "</pre>",
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


async def test_handle_message_routes_conversation_text_without_creating_entries() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "conversation_user")
    extraction_service = SimpleNamespace(extract=lambda _request: (_ for _ in ()).throw(AssertionError("extract must not be called")))
    captured_reply_args = {}

    def reply_stub(*, user_message, factual_context, session_summary, recent_turns, images=()):
        captured_reply_args["user_message"] = user_message
        captured_reply_args["factual_context"] = factual_context
        captured_reply_args["session_summary"] = session_summary
        captured_reply_args["recent_turns"] = recent_turns
        captured_reply_args["images"] = images
        return SimpleNamespace(text=f"Ответ на: {user_message}", updated_session_summary="обновлённый summary")

    conversation_service = SimpleNamespace(
        reply=reply_stub
    )
    message = SimpleNamespace(
        text="Как добрать белок без лишних калорий?",
        message_id=321,
        chat=SimpleNamespace(id=98765),
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="conversation_user"),
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=654321, chat=SimpleNamespace(id=98765))),
    )

    original_datetime = handle_message.__globals__["datetime"]

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)

    handle_message.__globals__["datetime"] = FixedDateTime
    try:
        await handle_message(
            message,
            session_factory,
            extraction_service=extraction_service,
            conversation_service=conversation_service,
            admin_user_ids=(ADMIN_ID,),
        )
    finally:
        handle_message.__globals__["datetime"] = original_datetime

    with session_factory() as session:
        assert session.query(Entry).count() == 0
        saved_session = session.query(ConversationSession).one()
        saved_messages = session.query(ConversationMessage).order_by(ConversationMessage.id.asc()).all()

    assert captured_reply_args["user_message"] == "Как добрать белок без лишних калорий?"
    factual_context = captured_reply_args["factual_context"]
    assert captured_reply_args["session_summary"] is None
    assert captured_reply_args["recent_turns"] == []
    assert captured_reply_args["images"] == ()
    assert factual_context.summary_date.isoformat() == "2026-05-20"
    assert factual_context.day_totals["water"] == 0.0
    assert factual_context.goal_progress["calories"].goal_value == 1800
    assert factual_context.recent_entries == []
    assert saved_session.summary_text == "обновлённый summary"
    assert [
        (saved_message.role.value, saved_message.content, saved_message.telegram_chat_id, saved_message.telegram_message_id)
        for saved_message in saved_messages
    ] == [
        ("user", "Как добрать белок без лишних калорий?", None, None),
        ("assistant", "Ответ на: Как добрать белок без лишних калорий?", 98765, 654321),
    ]
    message.answer.assert_awaited_once()
    message.bot.send_chat_action.assert_awaited_once_with(chat_id=98765, action="typing")
    assert message.answer.await_args.args == ("Ответ на: Как добрать белок без лишних калорий?",)
    assert message.answer.await_args.kwargs["parse_mode"] is None
    assert message.answer.await_args.kwargs["reply_to_message_id"] == 321


async def test_handle_message_sends_conversation_reply_as_plain_text() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "conversation_html_user")
    extraction_service = SimpleNamespace(extract=lambda _request: (_ for _ in ()).throw(AssertionError("extract must not be called")))
    conversation_service = SimpleNamespace(
        reply=lambda *, user_message, factual_context, session_summary, recent_turns, images=(): SimpleNamespace(
            text="Перед тренировкой лучше держать жиры <30 г и не переедать.",
            updated_session_summary="summary",
        )
    )
    message = SimpleNamespace(
        text="что лучше съесть перед вечерней тренировкой?",
        message_id=322,
        chat=SimpleNamespace(id=98766),
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="conversation_html_user"),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=654322, chat=SimpleNamespace(id=98766))),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        conversation_service=conversation_service,
        admin_user_ids=(ADMIN_ID,),
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Перед тренировкой лучше держать жиры <30 г и не переедать.",)
    assert message.answer.await_args.kwargs["parse_mode"] is None
    assert message.answer.await_args.kwargs["reply_to_message_id"] == 322


async def test_handle_message_returns_ambiguous_reply_without_creating_entries() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "ambiguous_user")
    extraction_service = SimpleNamespace(extract=lambda _request: (_ for _ in ()).throw(AssertionError("extract must not be called")))
    message_routing_service = SimpleNamespace(
        route=lambda _request, **_kwargs: MessageRoutingDecision(route="ambiguous", reason="test_ambiguous")
    )
    message = SimpleNamespace(
        text="Сегодня как-то странно с едой и режимом",
        message_id=654,
        chat=SimpleNamespace(id=98767),
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="ambiguous_user"),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        message_routing_service=message_routing_service,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        assert session.query(Entry).count() == 0

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (build_ambiguous_message_response(),)
    assert message.answer.await_args.kwargs["reply_to_message_id"] == 654


async def test_handle_message_does_not_save_workout_when_feature_is_disabled() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "workout_disabled_user")
    extraction_service = SimpleNamespace(
        extract=lambda _request: ValidExtractionPayload(
            payload=ExtractedJournalPayload(
                entries=[
                    ExtractedJournalEntry(
                        type=EntryType.WORKOUT,
                        items=[ExtractedJournalItem(name="бег", quantity=40, unit="мин")],
                    )
                ]
            ),
            extraction_provider="openai_responses",
            extraction_model="gpt-5-mini",
            raw_payload='{"entries":[{"type":"workout","items":[{"name":"бег","quantity":40,"unit":"мин"}]}]}',
        )
    )
    message = SimpleNamespace(
        text="тренировка: бег 40 минут",
        message_id=778,
        chat=SimpleNamespace(id=987680),
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="workout_disabled_user"),
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
    assert message.answer.await_args.args == (
        "Запись тренировок сейчас выключена. Включи её в /settings, если хочешь сохранять такие сообщения.",
    )


async def test_handle_message_saves_workout_when_feature_is_enabled() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "workout_enabled_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="workout_enabled_user", timezone="Europe/Moscow")
        user.workout_logging_enabled = True
        session.add(user)
        session.commit()

    extraction_service = SimpleNamespace(
        extract=lambda _request: ValidExtractionPayload(
            payload=ExtractedJournalPayload(
                entries=[
                    ExtractedJournalEntry(
                        type=EntryType.WORKOUT,
                        items=[ExtractedJournalItem(name="бег", quantity=40, unit="мин")],
                    )
                ]
            ),
            extraction_provider="openai_responses",
            extraction_model="gpt-5-mini",
            raw_payload='{"entries":[{"type":"workout","items":[{"name":"бег","quantity":40,"unit":"мин"}]}]}',
        )
    )
    message = SimpleNamespace(
        text="тренировка: бег 40 минут",
        message_id=779,
        chat=SimpleNamespace(id=987681),
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="workout_enabled_user"),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_entry = session.query(Entry).one()
        saved_item = session.query(EntryItem).one()

    assert saved_entry.entry_type == EntryType.WORKOUT
    assert saved_entry.source_text == "тренировка: бег 40 минут"
    assert saved_item.name == "бег"
    assert saved_item.quantity == 40
    assert saved_item.unit == "min"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Сохранил:\n- бег (40 мин)",)


async def test_handle_message_saves_workout_calorie_metric_from_photo_extraction() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "workout_photo_metric_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="workout_photo_metric_user", timezone="Europe/Moscow")
        user.workout_logging_enabled = True
        session.add(user)
        session.commit()

    extraction_service = SimpleNamespace(
        extract=lambda _request: ValidExtractionPayload(
            payload=ExtractedJournalPayload(
                entries=[
                    ExtractedJournalEntry(
                        type=EntryType.WORKOUT,
                        items=[
                            ExtractedJournalItem(
                                name="тренировка",
                                quantity=90,
                                unit="мин",
                                metrics=[ExtractedJournalMetric(code="workout_calories", value=757.0, confidence="high")],
                            )
                        ],
                    )
                ]
            ),
            extraction_provider="openai_responses",
            extraction_model="gpt-5-mini",
            raw_payload=(
                '{"entries":[{"type":"workout","items":[{"name":"тренировка","quantity":90,"unit":"мин",'
                '"metrics":[{"code":"workout_calories","value":757,"confidence":"high"}]}]}]}'
            ),
        )
    )

    async def download_stub(_photo, destination):
        destination.write(b"workout-image-bytes")

    message = SimpleNamespace(
        text=None,
        caption=None,
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")],
        message_id=780,
        chat=SimpleNamespace(id=987682),
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="workout_photo_metric_user"),
        bot=SimpleNamespace(download=AsyncMock(side_effect=download_stub)),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_entry = session.query(Entry).one()
        saved_item = session.query(EntryItem).one()
        saved_metric = session.query(EntryItemMetric).one()
        saved_metric_code = saved_metric.metric.code

    assert saved_entry.entry_type == EntryType.WORKOUT
    assert saved_item.name == "тренировка"
    assert saved_metric_code == "workout_calories"
    assert saved_metric.value == 757.0
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- тренировка (90 мин)\n- калории тренировки: 757.0 ккал",
    )


async def test_handle_message_rejects_photo_media_group_for_workout_screenshot_flow() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "workout_album_user")
    extraction_service = SimpleNamespace(
        extract=lambda _request: (_ for _ in ()).throw(AssertionError("extract must not be called for media group photos"))
    )

    async def download_stub(_photo, destination):
        destination.write(b"workout-image-bytes")

    message = SimpleNamespace(
        text=None,
        caption=None,
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")],
        media_group_id="album-1",
        message_id=781,
        chat=SimpleNamespace(id=987683),
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="workout_album_user"),
        bot=SimpleNamespace(download=AsyncMock(side_effect=download_stub)),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        admin_user_ids=(ADMIN_ID,),
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Пока я умею разбирать только одно изображение за раз. Пришли одно основное фото или один скриншот.",
    )


async def test_handle_message_rejects_photo_media_group_for_food_flow() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "food_album_user")
    extraction_service = SimpleNamespace(
        extract=lambda _request: (_ for _ in ()).throw(AssertionError("extract must not be called for media group photos"))
    )

    async def download_stub(_photo, destination):
        destination.write(b"food-image-bytes")

    message = SimpleNamespace(
        text=None,
        caption=None,
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")],
        media_group_id="album-2",
        message_id=782,
        chat=SimpleNamespace(id=987684),
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="food_album_user"),
        bot=SimpleNamespace(download=AsyncMock(side_effect=download_stub)),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        admin_user_ids=(ADMIN_ID,),
    )

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Пока я умею разбирать только одно изображение за раз. Пришли одно основное фото или один скриншот.",
    )


async def test_handle_message_does_not_route_slash_like_text_to_journal() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "slash_user")
    extraction_service = SimpleNamespace(extract=lambda _request: (_ for _ in ()).throw(AssertionError("extract must not be called")))
    message = SimpleNamespace(
        text="/админ",
        message_id=777,
        chat=SimpleNamespace(id=98768),
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="slash_user"),
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
    assert message.answer.await_args.args == (build_ambiguous_message_response(),)
    assert message.answer.await_args.kwargs["reply_to_message_id"] == 777


async def test_handle_message_routes_follow_up_to_conversation_when_active_session_exists() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "followup_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="followup_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        coach_session = ConversationSession(
            user_id=user.id,
            summary_text="говорили про питание перед тренировкой",
            started_at=datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 5, 20, 10, 30, tzinfo=timezone.utc),
        )
        session.add(coach_session)
        session.flush()
        session.add_all(
            [
                ConversationMessage(
                    session_id=coach_session.id,
                    role=ConversationMessageRole.USER,
                    content="что лучше съесть перед вечерней тренировкой?",
                    created_at=datetime(2026, 5, 20, 10, 29, tzinfo=timezone.utc),
                ),
                ConversationMessage(
                    session_id=coach_session.id,
                    role=ConversationMessageRole.ASSISTANT,
                    content="дам два сценария",
                    created_at=datetime(2026, 5, 20, 10, 30, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    extraction_service = SimpleNamespace(extract=lambda _request: (_ for _ in ()).throw(AssertionError("extract must not be called")))
    captured_reply_args = {}

    def reply_stub(*, user_message, factual_context, session_summary, recent_turns, images=()):
        captured_reply_args["user_message"] = user_message
        captured_reply_args["session_summary"] = session_summary
        captured_reply_args["recent_turns"] = recent_turns
        captured_reply_args["images"] = images
        return SimpleNamespace(text="уточняю сценарий А", updated_session_summary="обновлённый followup summary")

    message = SimpleNamespace(
        text="это будет сценарий А",
        message_id=888,
        chat=SimpleNamespace(id=98769),
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="followup_user"),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=654888, chat=SimpleNamespace(id=98769))),
    )

    original_datetime = handle_message.__globals__["datetime"]

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 5, 20, 10, 40, tzinfo=timezone.utc)

    handle_message.__globals__["datetime"] = FixedDateTime
    try:
        await handle_message(
            message,
            session_factory,
            extraction_service=extraction_service,
            conversation_service=SimpleNamespace(reply=reply_stub),
            admin_user_ids=(ADMIN_ID,),
        )
    finally:
        handle_message.__globals__["datetime"] = original_datetime

    with session_factory() as session:
        assert session.query(Entry).count() == 0
        saved_session = session.query(ConversationSession).one()
        saved_messages = session.query(ConversationMessage).order_by(ConversationMessage.id.asc()).all()

    assert captured_reply_args["user_message"] == "это будет сценарий А"
    assert captured_reply_args["session_summary"] == "говорили про питание перед тренировкой"
    assert captured_reply_args["images"] == ()
    assert [(turn.role, turn.content) for turn in captured_reply_args["recent_turns"]] == [
        ("user", "что лучше съесть перед вечерней тренировкой?"),
        ("assistant", "дам два сценария"),
    ]
    assert saved_session.summary_text == "обновлённый followup summary"
    assert [(saved_message.role.value, saved_message.content) for saved_message in saved_messages] == [
        ("user", "что лучше съесть перед вечерней тренировкой?"),
        ("assistant", "дам два сценария"),
        ("user", "это будет сценарий А"),
        ("assistant", "уточняю сценарий А"),
    ]
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("уточняю сценарий А",)


async def test_handle_message_routes_reply_to_coach_message_into_same_session_even_after_ttl() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "reply_followup_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="reply_followup_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        coach_session = ConversationSession(
            user_id=user.id,
            summary_text="говорили про предтренировочный перекус",
            started_at=datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 5, 20, 10, 30, tzinfo=timezone.utc),
        )
        session.add(coach_session)
        session.flush()
        session.add_all(
            [
                ConversationMessage(
                    session_id=coach_session.id,
                    role=ConversationMessageRole.USER,
                    content="что лучше съесть перед вечерней тренировкой?",
                    created_at=datetime(2026, 5, 20, 10, 29, tzinfo=timezone.utc),
                ),
                ConversationMessage(
                    session_id=coach_session.id,
                    role=ConversationMessageRole.ASSISTANT,
                    content="если через 10 минут, бери лёгкий быстрый перекус",
                    telegram_chat_id=321123,
                    telegram_message_id=654987,
                    created_at=datetime(2026, 5, 20, 10, 30, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    extraction_service = SimpleNamespace(extract=lambda _request: (_ for _ in ()).throw(AssertionError("extract must not be called")))
    captured_reply_args = {}

    def reply_stub(*, user_message, factual_context, session_summary, recent_turns, images=()):
        captured_reply_args["user_message"] = user_message
        captured_reply_args["session_summary"] = session_summary
        captured_reply_args["recent_turns"] = recent_turns
        captured_reply_args["images"] = images
        return SimpleNamespace(text="тогда бери только быстрые углеводы", updated_session_summary="уточнили быстрый перекус")

    message = SimpleNamespace(
        text="а если она через 10 минут?",
        message_id=889,
        chat=SimpleNamespace(id=321123),
        reply_to_message=SimpleNamespace(message_id=654987),
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="reply_followup_user"),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=654889, chat=SimpleNamespace(id=321123))),
    )

    original_datetime = handle_message.__globals__["datetime"]

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 5, 20, 12, 45, tzinfo=timezone.utc)

    handle_message.__globals__["datetime"] = FixedDateTime
    try:
        await handle_message(
            message,
            session_factory,
            extraction_service=extraction_service,
            conversation_service=SimpleNamespace(reply=reply_stub),
            admin_user_ids=(ADMIN_ID,),
        )
    finally:
        handle_message.__globals__["datetime"] = original_datetime

    with session_factory() as session:
        saved_session = session.query(ConversationSession).one()
        saved_messages = session.query(ConversationMessage).order_by(ConversationMessage.id.asc()).all()

    assert captured_reply_args["user_message"] == "а если она через 10 минут?"
    assert captured_reply_args["session_summary"] == "говорили про предтренировочный перекус"
    assert captured_reply_args["images"] == ()
    assert [(turn.role, turn.content) for turn in captured_reply_args["recent_turns"]] == [
        ("user", "что лучше съесть перед вечерней тренировкой?"),
        ("assistant", "если через 10 минут, бери лёгкий быстрый перекус"),
    ]
    assert saved_session.summary_text == "уточнили быстрый перекус"
    assert [(saved_message.role.value, saved_message.content) for saved_message in saved_messages] == [
        ("user", "что лучше съесть перед вечерней тренировкой?"),
        ("assistant", "если через 10 минут, бери лёгкий быстрый перекус"),
        ("user", "а если она через 10 минут?"),
        ("assistant", "тогда бери только быстрые углеводы"),
    ]
    assert saved_messages[-1].telegram_chat_id == 321123
    assert saved_messages[-1].telegram_message_id == 654889
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("тогда бери только быстрые углеводы",)


async def test_photo_message_with_explicit_coaching_caption_routes_to_conversation() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "photo_conversation_user")
    extraction_service = SimpleNamespace(extract=lambda _request: (_ for _ in ()).throw(AssertionError("extract must not be called")))
    captured_reply_args = {}

    def reply_stub(*, user_message, factual_context, session_summary, recent_turns, images=()):
        captured_reply_args["user_message"] = user_message
        captured_reply_args["images"] = images
        return SimpleNamespace(text="Из этого можно сделать лёгкий ужин с упором на овощи и обычный белок.", updated_session_summary="обсуждали продукты по фото")

    async def download_stub(_photo, destination):
        destination.write(b"fridge-image-bytes")

    message = SimpleNamespace(
        text=None,
        caption="что лучше приготовить из этого?",
        message_id=990,
        chat=SimpleNamespace(id=98770),
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")],
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="photo_conversation_user"),
        bot=SimpleNamespace(download=AsyncMock(side_effect=download_stub)),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=654990, chat=SimpleNamespace(id=98770))),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        conversation_service=SimpleNamespace(reply=reply_stub),
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        assert session.query(Entry).count() == 0
        saved_session = session.query(ConversationSession).one()

    assert captured_reply_args["user_message"] == "что лучше приготовить из этого?"
    assert len(captured_reply_args["images"]) == 1
    assert captured_reply_args["images"][0].data == b"fridge-image-bytes"
    assert saved_session.summary_text == "обсуждали продукты по фото"
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == ("Из этого можно сделать лёгкий ужин с упором на овощи и обычный белок.",)
    assert message.answer.await_args.kwargs["reply_to_message_id"] == 990


async def test_photo_message_with_journal_caption_stays_journal_even_with_active_conversation_session() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "photo_journal_active_session_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="photo_journal_active_session_user", timezone="Europe/Moscow")
        session.add(user)
        session.flush()
        coach_session = ConversationSession(
            user_id=user.id,
            summary_text="говорили про ужин",
            started_at=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc),
        )
        session.add(coach_session)
        session.commit()

    extraction_service = SimpleNamespace(
        extract=lambda _request: ValidExtractionPayload(
            payload=ExtractedJournalPayload(
                entries=[
                    ExtractedJournalEntry(
                        type=EntryType.FOOD,
                        items=[ExtractedJournalItem(name="курица"), ExtractedJournalItem(name="кускус")],
                    )
                ]
            ),
            extraction_provider="openai_responses",
            extraction_model="gpt-5-mini",
            raw_payload='{"entries":[{"type":"food","items":[{"name":"курица"},{"name":"кускус"}]}]}',
        )
    )
    nutrition_service = StaticNutritionEstimationService(
        raw_payload=build_metric_payload(["entry-1:item-0", "entry-1:item-1"])
    )

    async def download_stub(_photo, destination):
        destination.write(b"meal-image-bytes")

    message = SimpleNamespace(
        text=None,
        caption="Запиши в обед",
        message_id=991,
        chat=SimpleNamespace(id=98771),
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")],
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="photo_journal_active_session_user"),
        bot=SimpleNamespace(download=AsyncMock(side_effect=download_stub), send_chat_action=AsyncMock()),
        answer=AsyncMock(),
    )

    original_datetime = handle_message.__globals__["datetime"]

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 5, 20, 12, 40, tzinfo=timezone.utc)

    handle_message.__globals__["datetime"] = FixedDateTime
    try:
        await handle_message(
            message,
            session_factory,
            extraction_service=extraction_service,
            nutrition_service=nutrition_service,
            conversation_service=SimpleNamespace(reply=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("reply must not be called"))),
            admin_user_ids=(ADMIN_ID,),
        )
    finally:
        handle_message.__globals__["datetime"] = original_datetime

    with session_factory() as session:
        saved_entries = session.query(Entry).all()
        saved_messages = session.query(ConversationMessage).all()

    assert len(saved_entries) == 1
    assert saved_entries[0].entry_type == EntryType.FOOD
    assert saved_messages == []
    message.answer.assert_awaited_once()
    assert "Сохранил:" in message.answer.await_args.args[0]


async def test_workout_photo_with_write_caption_stays_journal_and_is_not_routed_to_conversation() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "workout_photo_caption_user")
    with session_factory() as session:
        user = User(telegram_user_id=ALLOWED_USER_ID, username="workout_photo_caption_user", timezone="Europe/Moscow")
        user.workout_logging_enabled = True
        session.add(user)
        session.commit()

    extraction_service = SimpleNamespace(
        extract=lambda _request: ValidExtractionPayload(
            payload=ExtractedJournalPayload(
                entries=[
                    ExtractedJournalEntry(
                        type=EntryType.WORKOUT,
                        items=[
                            ExtractedJournalItem(
                                name="тренировка",
                                quantity=90,
                                unit="мин",
                                metrics=[ExtractedJournalMetric(code="workout_calories", value=757.0, confidence="high")],
                            )
                        ],
                    )
                ]
            ),
            extraction_provider="openai_responses",
            extraction_model="gpt-5-mini",
            raw_payload=(
                '{"entries":[{"type":"workout","items":[{"name":"тренировка","quantity":90,"unit":"мин",'
                '"metrics":[{"code":"workout_calories","value":757,"confidence":"high"}]}]}]}'
            ),
        )
    )

    async def download_stub(_photo, destination):
        destination.write(b"workout-image-bytes")

    message = SimpleNamespace(
        text=None,
        caption="запиши тренировку",
        message_id=992,
        chat=SimpleNamespace(id=98772),
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="large")],
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="workout_photo_caption_user"),
        bot=SimpleNamespace(download=AsyncMock(side_effect=download_stub), send_chat_action=AsyncMock()),
        answer=AsyncMock(),
    )

    await handle_message(
        message,
        session_factory,
        extraction_service=extraction_service,
        conversation_service=SimpleNamespace(reply=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("reply must not be called"))),
        admin_user_ids=(ADMIN_ID,),
    )

    with session_factory() as session:
        saved_entries = session.query(Entry).all()
        saved_messages = session.query(ConversationMessage).all()

    assert len(saved_entries) == 1
    assert saved_entries[0].entry_type == EntryType.WORKOUT
    assert saved_messages == []
    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (
        "Сохранил:\n- тренировка (90 мин)\n- калории тренировки: 757.0 ккал",
    )


async def test_handle_message_routes_clear_journal_text_to_extraction_flow() -> None:
    session_factory = create_session_factory()
    allow_user(session_factory, ALLOWED_USER_ID, "journal_route_user")
    extraction_service = SimpleNamespace(
        extract=lambda _request: ValidExtractionPayload(
            payload=ExtractedJournalPayload(
                entries=[
                    ExtractedJournalEntry(
                        type=EntryType.FOOD,
                        items=[ExtractedJournalItem(name="гречка"), ExtractedJournalItem(name="курица")],
                    )
                ]
            ),
            extraction_provider="openai_responses",
            extraction_model="gpt-5-mini",
            raw_payload='{"entries":[{"type":"food","items":[{"name":"гречка"},{"name":"курица"}]}]}',
        )
    )
    nutrition_service = StaticNutritionEstimationService(
        raw_payload=build_metric_payload(["entry-1:item-0", "entry-1:item-1"])
    )
    message = SimpleNamespace(
        text="съел гречку с курицей",
        from_user=SimpleNamespace(id=ALLOWED_USER_ID, username="journal_route_user"),
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
        assert session.query(Entry).count() == 1
        assert session.query(EntryItem).count() == 2

    message.answer.assert_awaited_once()
