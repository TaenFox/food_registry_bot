import asyncio

from food_registry_bot.bot.factory import create_bot, create_dispatcher
from food_registry_bot.config import get_settings
from food_registry_bot.db.session import create_session_factory


async def run() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    bot = create_bot(settings.bot_token)
    dispatcher = create_dispatcher(create_session_factory())
    await dispatcher.start_polling(bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
