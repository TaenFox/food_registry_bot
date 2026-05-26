import asyncio
import logging

from food_registry_bot.bot.commands import setup_bot_commands
from food_registry_bot.bot.factory import create_bot, create_dispatcher
from food_registry_bot.conversation.factory import create_conversation_service
from food_registry_bot.config import get_settings
from food_registry_bot.db.session import create_session_factory
from food_registry_bot.extraction.factory import create_extraction_service
from food_registry_bot.nutrition.factory import create_nutrition_service

logger = logging.getLogger(__name__)


def build_startup_notification(*, app_version: str) -> str:
    return (
        "Бот запущен.\n"
        f"Версия: {app_version}"
    )


async def notify_admins_about_startup(
    *,
    bot,
    admin_user_ids: tuple[int, ...],
    app_version: str,
) -> None:
    notification_text = build_startup_notification(app_version=app_version)
    for admin_user_id in admin_user_ids:
        try:
            await bot.send_message(admin_user_id, notification_text)
        except Exception:
            logger.exception("Failed to send startup notification to admin %s", admin_user_id)


async def run() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    bot = create_bot(settings.bot_token)
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
    )
    await dispatcher.start_polling(bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
