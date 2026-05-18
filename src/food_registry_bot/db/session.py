from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from food_registry_bot.config import get_settings


def create_engine_from_settings() -> Engine:
    settings = get_settings()
    return create_engine(settings.sqlalchemy_database_url, future=True)
