from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand

logger = logging.getLogger(__name__)

USER_COMMAND_SPECS: tuple[tuple[str, str], ...] = (
    ("start", "начать работу с ботом"),
    ("today", "показать итог за текущий день"),
    ("recent", "показать последние записи"),
    ("report", "показать отчёт за период"),
    ("goal", "посмотреть или изменить цели"),
    ("settings", "настроить отображение summary"),
    ("files", "управлять файлами импорта и экспорта"),
    ("health", "проверить доступность бота"),
)

ADMIN_COMMAND_SPECS: tuple[tuple[str, str], ...] = (
    ("admin", "показать состояние системы"),
    ("admin_backfill_nutrition", "дозаполнить неполные nutrition metrics"),
    ("admin_llm_errors", "показать последние LLM-ошибки"),
)


def build_user_bot_commands() -> list[BotCommand]:
    return [BotCommand(command=command, description=description) for command, description in USER_COMMAND_SPECS]


def build_admin_bot_commands() -> list[BotCommand]:
    return [BotCommand(command=command, description=description) for command, description in ADMIN_COMMAND_SPECS]


async def setup_bot_commands(bot: Bot, *, admin_user_ids: tuple[int, ...]) -> None:
    del admin_user_ids

    try:
        await bot.set_my_commands(build_user_bot_commands())
    except Exception:
        logger.exception("Failed to register bot commands")
