from __future__ import annotations

from unittest.mock import AsyncMock, call

from aiogram.types import BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault

from food_registry_bot.bot.commands import (
    ADMIN_COMMAND_SPECS,
    USER_COMMAND_SPECS,
    build_admin_bot_commands,
    build_user_bot_commands,
    setup_bot_commands,
)


def test_build_user_bot_commands_matches_documented_commands() -> None:
    commands = build_user_bot_commands()

    assert [(command.command, command.description) for command in commands] == list(USER_COMMAND_SPECS)


def test_build_admin_bot_commands_matches_documented_commands() -> None:
    commands = build_admin_bot_commands()

    assert [(command.command, command.description) for command in commands] == [
        *list(USER_COMMAND_SPECS),
        *list(ADMIN_COMMAND_SPECS),
    ]


async def test_setup_bot_commands_registers_default_and_admin_scopes() -> None:
    bot = AsyncMock()

    await setup_bot_commands(bot, admin_user_ids=(101, 202))

    assert bot.set_my_commands.await_args_list == [
        call(build_user_bot_commands(), scope=BotCommandScopeDefault()),
        call(build_user_bot_commands(), scope=BotCommandScopeAllPrivateChats()),
        call(build_admin_bot_commands(), scope=BotCommandScopeChat(chat_id=101)),
        call(build_admin_bot_commands(), scope=BotCommandScopeChat(chat_id=202)),
    ]


def test_build_user_bot_commands_excludes_button_replaced_commands() -> None:
    commands = build_user_bot_commands()

    assert [command.command for command in commands] == ["start", "provider", "context", "files", "ping"]
