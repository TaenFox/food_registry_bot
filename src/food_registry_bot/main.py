import asyncio

from food_registry_bot.bot.factory import create_bot, create_dispatcher
from food_registry_bot.conversation.factory import create_conversation_service
from food_registry_bot.config import get_settings
from food_registry_bot.db.session import create_session_factory
from food_registry_bot.extraction.factory import create_extraction_service
from food_registry_bot.nutrition.factory import create_nutrition_service


async def run() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    bot = create_bot(settings.bot_token)
    dispatcher = create_dispatcher(
        create_session_factory(),
        extraction_service=create_extraction_service(settings),
        nutrition_service=create_nutrition_service(settings),
        conversation_service=create_conversation_service(settings),
        admin_user_ids=settings.admin_user_ids,
        app_version=settings.app_version,
    )
    await dispatcher.start_polling(bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
