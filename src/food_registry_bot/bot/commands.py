from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault

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
)


def build_user_bot_commands() -> list[BotCommand]:
    return [BotCommand(command=command, description=description) for command, description in USER_COMMAND_SPECS]


def build_admin_bot_commands() -> list[BotCommand]:
    return [BotCommand(command=command, description=description) for command, description in ADMIN_COMMAND_SPECS]


async def setup_bot_commands(bot: Bot, *, admin_user_ids: tuple[int, ...]) -> None:
    try:
        user_commands = build_user_bot_commands()
        admin_commands = build_admin_bot_commands()

        await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
        await bot.set_my_commands(user_commands, scope=BotCommandScopeAllPrivateChats())

        for admin_user_id in admin_user_ids:
            await bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=admin_user_id),
            )
    except Exception:
        logger.exception("Failed to register bot commands")
