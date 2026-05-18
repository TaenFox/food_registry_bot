from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_env: str = "local"
    bot_token: Optional[str] = Field(default=None, alias="BOT_TOKEN")

    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="food_registry", alias="POSTGRES_DB")
    postgres_user: str = Field(default="food_registry", alias="POSTGRES_USER")
    postgres_password: str = Field(default="food_registry", alias="POSTGRES_PASSWORD")
    database_url: Optional[str] = Field(default=None, alias="DATABASE_URL")

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url:
            return self.database_url

        return (
            "postgresql+psycopg://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
