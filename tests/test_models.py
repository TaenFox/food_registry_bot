from food_registry_bot.db.base import Base
from food_registry_bot.db import models  # noqa: F401


def test_core_tables_registered() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "user_access",
        "user_llm_profiles",
        "user_llm_connections",
        "user_goal_preferences",
        "user_summary_preferences",
        "daily_goal_snapshots",
        "entries",
        "entry_items",
        "supported_diets",
        "user_diet_preferences",
        "supported_metrics",
        "entry_item_metrics",
        "callback_states",
        "llm_issue_logs",
        "conversation_sessions",
        "conversation_messages",
        "data_exchange_files",
    }
