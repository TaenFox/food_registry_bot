from __future__ import annotations

import asyncio
import logging

from food_registry_bot.bot.commands import setup_bot_commands
from food_registry_bot.bot.factory import create_bot, create_dispatcher
from food_registry_bot.conversation.factory import create_conversation_service
from food_registry_bot.config import get_settings
from food_registry_bot.db.session import create_session_factory
from food_registry_bot.extraction.factory import create_extraction_service
from food_registry_bot.nutrition.factory import create_nutrition_service
from food_registry_bot.runtime_state import (
    RestartMarker,
    clear_restart_marker,
    clear_runtime_state,
    read_restart_marker,
    runtime_state_exists,
    write_restart_marker,
    write_runtime_state,
)
from food_registry_bot.watchdog import HeartbeatMonitor

logger = logging.getLogger(__name__)


def build_startup_notification(
    *,
    app_version: str,
    restart_marker: RestartMarker | None = None,
) -> str:
    message = (
        "Бот запущен.\n"
        f"Версия: {app_version}"
    )
    if restart_marker is not None:
        message += (
            "\n"
            "Предыдущий запуск завершился нештатно.\n"
            f"Причина: {restart_marker.reason}\n"
            f"Когда зафиксировано: {restart_marker.recorded_at}"
        )
    return message


async def notify_admins_about_startup(
    *,
    bot,
    admin_user_ids: tuple[int, ...],
    app_version: str,
    restart_marker: RestartMarker | None = None,
) -> None:
    notification_text = build_startup_notification(
        app_version=app_version,
        restart_marker=restart_marker,
    )
    for admin_user_id in admin_user_ids:
        try:
            await bot.send_message(admin_user_id, notification_text)
        except Exception:
            logger.exception("Failed to send startup notification to admin %s", admin_user_id)


async def run() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    restart_marker = read_restart_marker(settings.restart_marker_file)
    if restart_marker is None and runtime_state_exists(settings.runtime_state_file):
        restart_marker = RestartMarker(
            reason="previous process did not finish graceful shutdown",
            recorded_at="unknown",
        )
        write_restart_marker(
            settings.restart_marker_file,
            reason=restart_marker.reason,
        )

    write_runtime_state(settings.runtime_state_file)

    heartbeat_monitor: HeartbeatMonitor | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    if settings.watchdog_enabled:
        heartbeat_monitor = HeartbeatMonitor(
            heartbeat_file=settings.heartbeat_file,
            heartbeat_interval_seconds=settings.watchdog_heartbeat_interval_seconds,
            timeout_seconds=settings.watchdog_timeout_seconds,
            restart_marker_file=settings.restart_marker_file,
        )
        heartbeat_monitor.beat()
        heartbeat_monitor.start_watchdog()
        heartbeat_task = asyncio.create_task(heartbeat_monitor.run_heartbeat_loop())

    bot = create_bot(settings.bot_token)
    try:
        await setup_bot_commands(bot, admin_user_ids=settings.admin_user_ids)
        dispatcher = create_dispatcher(
            create_session_factory(),
            extraction_service=create_extraction_service(settings),
            nutrition_service=create_nutrition_service(settings),
            conversation_service=create_conversation_service(settings),
            admin_user_ids=settings.admin_user_ids,
            settings=settings,
            app_version=settings.app_version,
        )
        await notify_admins_about_startup(
            bot=bot,
            admin_user_ids=settings.admin_user_ids,
            app_version=settings.app_version,
            restart_marker=restart_marker,
        )
        if restart_marker is not None:
            clear_restart_marker(settings.restart_marker_file)
        await dispatcher.start_polling(bot)
    except Exception as exc:
        write_restart_marker(
            settings.restart_marker_file,
            reason=f"process crashed with {type(exc).__name__}",
        )
        raise
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        if heartbeat_monitor is not None:
            heartbeat_monitor.stop()
        clear_runtime_state(settings.runtime_state_file)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
