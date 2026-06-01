from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import subprocess
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import enum

from food_registry_bot import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRETS_DIR = PROJECT_ROOT.parent / f"{PROJECT_ROOT.name}_local"
SECRETS_ENV_FILE = SECRETS_DIR / ".env"


def get_project_root() -> Path:
    return PROJECT_ROOT


def get_secrets_dir() -> Path:
    return SECRETS_DIR


def get_secrets_env_file() -> Path:
    return SECRETS_ENV_FILE


def resolve_default_data_exchange_dir() -> Path:
    return Path.cwd() / "var" / "data_exchange"


def get_data_exchange_dir() -> Path:
    return get_settings().data_exchange_dir


def detect_git_app_version(project_root: Path = PROJECT_ROOT) -> str | None:
    commands = (
        ["git", "-C", str(project_root), "describe", "--exact-match", "--tags", "HEAD"],
        ["git", "-C", str(project_root), "branch", "--show-current"],
        ["git", "-C", str(project_root), "describe", "--tags", "--always", "--dirty"],
    )

    for command in commands:
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

        version = result.stdout.strip()
        if version:
            return version

    return None


def resolve_app_version() -> str:
    git_version = detect_git_app_version()
    if git_version is not None:
        return git_version
    return __version__


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
    app_version: str = Field(default_factory=resolve_app_version, alias="APP_VERSION")
    bot_token: Optional[str] = Field(default=None, alias="BOT_TOKEN")
    admin_user_ids_raw: str = Field(default="", alias="ADMIN_USER_IDS")
    extraction_provider: ExtractionProvider = Field(
        default=ExtractionProvider.STRUCTURED_PAYLOAD,
        alias="EXTRACTION_PROVIDER",
    )
    llm_model: str = Field(default="gpt-5-mini", alias="LLM_MODEL")
    nutrition_provider: NutritionProvider = Field(
        default=NutritionProvider.LLM,
        alias="NUTRITION_PROVIDER",
    )
    nutrition_model: str = Field(default="gpt-5-mini", alias="NUTRITION_MODEL")
    conversation_model: str = Field(default="gpt-5-mini", alias="CONVERSATION_MODEL")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    enable_openai_provider: bool = Field(default=True, alias="ENABLE_OPENAI_PROVIDER")
    enable_mistral_provider: bool = Field(default=False, alias="ENABLE_MISTRAL_PROVIDER")
    personal_api_keys_secret: Optional[str] = Field(
        default=None,
        alias="PERSONAL_API_KEYS_SECRET",
    )
    data_exchange_dir: Path = Field(
        default_factory=resolve_default_data_exchange_dir,
        alias="DATA_EXCHANGE_DIR",
    )

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

    @property
    def admin_user_ids(self) -> tuple[int, ...]:
        if not self.admin_user_ids_raw.strip():
            return ()

        values = []
        for raw_value in self.admin_user_ids_raw.split(","):
            stripped_value = raw_value.strip()
            if not stripped_value:
                continue
            values.append(int(stripped_value))
        return tuple(values)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
