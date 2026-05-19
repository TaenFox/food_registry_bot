from food_registry_bot.db.base import Base
from food_registry_bot.db import models  # noqa: F401


def test_core_tables_registered() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "user_access",
        "entries",
        "entry_items",
        "supported_metrics",
        "entry_item_metrics",
    }
