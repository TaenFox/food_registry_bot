from pathlib import Path

from food_registry_bot import __version__
from food_registry_bot.config import Settings, get_data_exchange_dir, resolve_app_version


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


def test_detect_git_app_version_prefers_exact_tag(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, check, capture_output, text):
        calls.append(command)

        class Result:
            def __init__(self, stdout: str) -> None:
                self.stdout = stdout

        if "--exact-match" in command:
            return Result("v1.0.0\n")
        raise AssertionError("No other git command should be used after exact tag match")

    monkeypatch.setattr("food_registry_bot.config.subprocess.run", fake_run)

    from food_registry_bot.config import detect_git_app_version

    assert detect_git_app_version() == "v1.0.0"
    assert len(calls) == 1


def test_settings_data_exchange_dir_defaults_to_cwd(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATA_EXCHANGE_DIR", raising=False)

    settings = Settings(_env_file=None)

    assert settings.data_exchange_dir == tmp_path / "var" / "data_exchange"


def test_get_data_exchange_dir_respects_environment_value(monkeypatch, tmp_path: Path) -> None:
    expected_dir = tmp_path / "exchange"
    monkeypatch.setenv("DATA_EXCHANGE_DIR", str(expected_dir))
    get_data_exchange_dir.__globals__["get_settings"].cache_clear()

    try:
        assert get_data_exchange_dir() == expected_dir
    finally:
        get_data_exchange_dir.__globals__["get_settings"].cache_clear()
