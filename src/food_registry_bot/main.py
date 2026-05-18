from food_registry_bot.bot.factory import create_bot
from food_registry_bot.config import get_settings


def main() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    bot = create_bot(settings.bot_token)
    print(f"Configured bot @{bot.id or 'uninitialized'} for env={settings.app_env}")


if __name__ == "__main__":
    main()
