from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from food_registry_bot.bot.trial_notifications import notify_expired_trials
from food_registry_bot.db.base import Base
from food_registry_bot.db.models import AccountCategory
from food_registry_bot.db.repositories import UserAccessRepository
from food_registry_bot.llm_access.trial import TrialQuota


def test_trial_quota_has_separate_limits_and_keeps_the_current_reset_when_settings_change():
    quota = TrialQuota()
    now = datetime(2026, 11, 26, 23, 58, tzinfo=timezone.utc)
    reset = now + timedelta(minutes=2)
    for _ in range(7):
        assert quota.consume(1, "food", now=now, timezone_name="Asia/Tbilisi", start_hour=4) is None
    assert quota.consume(1, "food", now=now, timezone_name="UTC", start_hour=6) == reset

    def send_message(_):
        return quota.consume(1, "conversation", now=now, timezone_name="Asia/Tbilisi", start_hour=4)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(send_message, range(24)))
    assert results.count(None) == 20
    assert results.count(reset) == 4
    assert quota.remaining(1, now) == {"food": 0, "conversation": 0}
    assert quota.remaining(2, now) == {"food": 7, "conversation": 20}
    assert quota.remaining(1, reset) == {"food": 7, "conversation": 20}
    assert quota.consume(1, "food", now=reset, timezone_name="UTC", start_hour=6) is None


async def test_trial_expiry_notifies_once_and_retries_only_if_sending_failed():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    with factory.begin() as session:
        repo = UserAccessRepository(session)
        repo.start_trial_if_new(telegram_user_id=1, username="automatic", now=now - timedelta(days=7))
        repo.set_access(telegram_user_id=2, username=None, is_allowed=True)
        repo.grant_temporary_internal(telegram_user_id=2, username=None, now=now - timedelta(days=1))
        repo.start_trial_if_new(telegram_user_id=3, username="active", now=now)

    bot = AsyncMock()
    bot.send_message.side_effect = [RuntimeError("temporary delivery error"), None, None]
    options = {"bot": bot, "session_factory": factory, "admin_user_id": 99, "now": now}
    await notify_expired_trials(**options)
    with factory() as session:
        repo = UserAccessRepository(session)
        assert repo.get_by_telegram_user_id(1).temporary_internal_until is not None
        assert repo.get_by_telegram_user_id(2).temporary_internal_until is None

    await notify_expired_trials(**options)
    await notify_expired_trials(**options)
    messages = [call.args for call in bot.send_message.await_args_list]
    assert [chat for chat, _ in messages] == [99, 99, 99]
    assert "Пользователь: @automatic (ID 1)" in messages[0][1]
    assert "Пользователь: ID 2" in messages[1][1]
    assert messages[2] == messages[0]
    with factory.begin() as session:
        repo = UserAccessRepository(session)
        access = repo.get_by_telegram_user_id(1)
        assert access.temporary_internal_until is None
        assert access.is_allowed and access.account_category is AccountCategory.EXTERNAL
        assert repo.get_by_telegram_user_id(3).temporary_internal_until is not None
        assert repo.start_trial_if_new(telegram_user_id=1, username="automatic", now=now) is None


async def test_trial_expiry_does_not_clear_a_concurrent_renewal():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    with factory.begin() as session:
        UserAccessRepository(session).start_trial_if_new(
            telegram_user_id=1, username="renewed", now=now - timedelta(days=7),
        )

    async def renew_while_sending(*args, **kwargs):
        with factory.begin() as session:
            UserAccessRepository(session).grant_temporary_internal(
                telegram_user_id=1, username="renewed", now=now,
            )

    bot = AsyncMock()
    bot.send_message.side_effect = renew_while_sending
    await notify_expired_trials(bot=bot, session_factory=factory, admin_user_id=99, now=now)
    with factory() as session:
        repo = UserAccessRepository(session)
        access = repo.get_by_telegram_user_id(1)
        assert repo.normalize_datetime(access.temporary_internal_until) == now + timedelta(days=1)
