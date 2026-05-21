from food_registry_bot import __version__
from food_registry_bot.config import Settings, resolve_app_version


def test_resolve_app_version_prefers_git_description(monkeypatch) -> None:
    monkeypatch.setattr("food_registry_bot.config.detect_git_app_version", lambda project_root=None: "abc123")

    assert resolve_app_version() == "abc123"


def test_resolve_app_version_falls_back_to_package_version(monkeypatch) -> None:
    monkeypatch.setattr("food_registry_bot.config.detect_git_app_version", lambda project_root=None: None)

    assert resolve_app_version() == __version__


def test_settings_app_version_prefers_environment_value(monkeypatch) -> None:
    monkeypatch.setenv("APP_VERSION", "docker-build-42")

    settings = Settings(_env_file=None)

    assert settings.app_version == "docker-build-42"


def test_settings_app_version_uses_detected_git_value_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.setattr("food_registry_bot.config.detect_git_app_version", lambda project_root=None: "5a8ade0")

    settings = Settings(_env_file=None)

    assert settings.app_version == "5a8ade0"
