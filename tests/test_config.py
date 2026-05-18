from food_registry_bot.config import get_project_root, get_secrets_dir, get_secrets_env_file


def test_project_root_points_to_repository() -> None:
    assert get_project_root().name == "food_registry_bot"


def test_secrets_dir_is_project_specific_sibling_directory() -> None:
    project_root = get_project_root()

    assert get_secrets_dir() == project_root.parent / "food_registry_bot_local"


def test_secrets_env_file_lives_inside_sibling_secrets_directory() -> None:
    assert get_secrets_env_file() == get_secrets_dir() / ".env"
