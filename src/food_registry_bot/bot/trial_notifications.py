from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.repositories import UserAccessRepository

logger = logging.getLogger(__name__)


async def notify_expired_trials(
    *, bot: Bot, session_factory: sessionmaker[Session], admin_user_id: int, now: datetime,
) -> None:
    with session_factory() as session:
        expired = [
            (access.telegram_user_id, access.username, access.temporary_internal_until)
            for access in UserAccessRepository(session).list_expired_trials(now=now)
        ]

    for telegram_user_id, username, expires_at in expired:
        if expires_at is None:
            continue
        user = f"@{username} (ID {telegram_user_id})" if username else f"ID {telegram_user_id}"
        ended = UserAccessRepository.normalize_datetime(expires_at)
        try:
            await bot.send_message(
                admin_user_id,
                f"Пробный доступ к ИИ закончился.\nПользователь: {user}\nОкончание: {ended:%d.%m.%Y в %H:%M} UTC.",
                parse_mode=None,
            )
        except Exception:
            logger.exception("Failed to notify admin about the trial of user %s", telegram_user_id)
            continue

        with session_factory.begin() as session:
            UserAccessRepository(session).clear_notified_trial(
                telegram_user_id=telegram_user_id, expires_at=expires_at,
            )


async def run_trial_expiry_notifications(
    *, bot: Bot, session_factory: sessionmaker[Session], admin_user_id: int,
) -> None:
    while True:
        try:
            await notify_expired_trials(
                bot=bot, session_factory=session_factory, admin_user_id=admin_user_id,
                now=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("Failed to check expired trials")
        await asyncio.sleep(60)
