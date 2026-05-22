from __future__ import annotations

from unittest.mock import AsyncMock

from food_registry_bot.main import build_startup_notification, notify_admins_about_startup


def test_build_startup_notification_includes_version() -> None:
    assert build_startup_notification(app_version="feature/import-export") == (
        "Бот запущен.\n"
        "Версия: feature/import-export"
    )


async def test_notify_admins_about_startup_sends_message_to_all_admins() -> None:
    bot = AsyncMock()

    await notify_admins_about_startup(
        bot=bot,
        admin_user_ids=(1001, 1002),
        app_version="v1.2.3",
    )

    assert bot.send_message.await_count == 2
    assert bot.send_message.await_args_list[0].args == (1001, "Бот запущен.\nВерсия: v1.2.3")
    assert bot.send_message.await_args_list[1].args == (1002, "Бот запущен.\nВерсия: v1.2.3")


async def test_notify_admins_about_startup_continues_after_single_send_failure() -> None:
    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=[RuntimeError("boom"), None])

    await notify_admins_about_startup(
        bot=bot,
        admin_user_ids=(1001, 1002),
        app_version="v1.2.3",
    )

    assert bot.send_message.await_count == 2
