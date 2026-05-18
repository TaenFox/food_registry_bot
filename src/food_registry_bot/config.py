from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import enum

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRETS_DIR = PROJECT_ROOT.parent / f"{PROJECT_ROOT.name}_local"
SECRETS_ENV_FILE = SECRETS_DIR / ".env"


def get_project_root() -> Path:
    return PROJECT_ROOT


def get_secrets_dir() -> Path:
    return SECRETS_DIR


def get_secrets_env_file() -> Path:
    return SECRETS_ENV_FILE


class ExtractionProvider(str, enum.Enum):
    STRUCTURED_PAYLOAD = "structured_payload"
    LLM = "llm"


class NutritionProvider(str, enum.Enum):
    STATIC = "static"
    LLM = "llm"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=SECRETS_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    app_env: str = "local"
    bot_token: Optional[str] = Field(default=None, alias="BOT_TOKEN")
    extraction_provider: ExtractionProvider = Field(
        default=ExtractionProvider.STRUCTURED_PAYLOAD,
        alias="EXTRACTION_PROVIDER",
    )
    llm_model: str = Field(default="gpt-5-mini", alias="LLM_MODEL")
    nutrition_provider: NutritionProvider = Field(
        default=NutritionProvider.STATIC,
        alias="NUTRITION_PROVIDER",
    )
    nutrition_model: str = Field(default="gpt-5-mini", alias="NUTRITION_MODEL")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")

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
