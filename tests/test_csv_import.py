from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import DailyGoalSnapshot, EntryType, MealType
from food_registry_bot.db.repositories import UserRepository
from food_registry_bot.db.models import SupportedMetric
from food_registry_bot.importing.csv_import import CsvNutritionImporter
from food_registry_bot.nutrition import DailyNutritionSummaryUseCase, DailyWaterSummaryUseCase


def create_test_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)()
    session.add_all(
        [
            SupportedMetric(code="calories", name="Calories", unit="kcal"),
            SupportedMetric(code="protein", name="Protein", unit="g"),
            SupportedMetric(code="fat", name="Fat", unit="g"),
            SupportedMetric(code="carbs", name="Carbs", unit="g"),
            SupportedMetric(code="fiber", name="Fiber", unit="g"),
        ]
    )
    session.commit()
    return session


def test_csv_import_parses_food_and_water_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "import.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Дата,Приём пищи,Блюдо / продукт,Категории,Количество,Единица,Ккал,\"Белки, г\",\"Жиры, г\",\"Углеводы, г\",\"Клетчатка, г\",Комментарий,Источник оценки,Уверенность оценки",
                "2026-05-20,Завтрак,Творог 5%,завтрак,100,г,\"121,0\",\"17,0\",\"5,0\",\"3,0\",\"0,0\",По справочнику,справочник,средняя",
                "2026-05-20,Напиток,Вода,\"напиток, гидратация\",250,мл,\"0,0\",\"0,0\",\"0,0\",\"0,0\",\"0,0\",Стакан воды,описание,высокая",
            ]
        ),
        encoding="utf-8",
    )

    rows = CsvNutritionImporter.read_csv(csv_path)

    assert len(rows) == 2
    assert rows[0].entry_type is EntryType.FOOD
    assert rows[0].meal_type is MealType.BREAKFAST
    assert rows[0].metrics["calories"] == 121.0
    assert rows[0].confidence == "medium"
    assert rows[1].entry_type is EntryType.WATER
    assert rows[1].item_name == "water"
    assert rows[1].unit == "ml"
    assert rows[1].confidence == "high"


def test_csv_import_creates_entries_metrics_and_goal_snapshots(tmp_path: Path) -> None:
    csv_path = tmp_path / "import.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Дата,Приём пищи,Блюдо / продукт,Категории,Количество,Единица,Ккал,\"Белки, г\",\"Жиры, г\",\"Углеводы, г\",\"Клетчатка, г\",Комментарий,Источник оценки,Уверенность оценки",
                "2026-05-20,Завтрак,Творог 5%,завтрак,100,г,\"121,0\",\"17,0\",\"5,0\",\"3,0\",\"0,0\",По справочнику,справочник,средняя",
                "2026-05-20,Напиток,Вода,\"напиток, гидратация\",250,мл,\"0,0\",\"0,0\",\"0,0\",\"0,0\",\"0,0\",Стакан воды,описание,высокая",
                "2026-05-21,Ужин,Рис отварной,ужин,100,г,\"123,0\",\"2,4\",\"0,3\",\"27,0\",\"0,4\",Рис,описание,средняя",
            ]
        ),
        encoding="utf-8",
    )
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4242, username="import_user")

    rows = CsvNutritionImporter.read_csv(csv_path)
    result = CsvNutritionImporter(session).import_rows(user=user, rows=rows)
    session.commit()

    assert result.imported_row_count == 3
    assert result.created_entry_count == 3
    assert result.created_snapshot_count == 2

    nutrition_summary = DailyNutritionSummaryUseCase(session).run(
        user_id=user.id,
        timezone_name=user.timezone,
        summary_date=date(2026, 5, 20),
    )
    water_summary = DailyWaterSummaryUseCase(session).run(
        user_id=user.id,
        timezone_name=user.timezone,
        summary_date=date(2026, 5, 20),
    )

    assert nutrition_summary.included_entry_count == 1
    assert nutrition_summary.totals.calories == 121.0
    assert water_summary.total_ml == 250
    assert water_summary.included_entry_count == 1
    assert session.query(DailyGoalSnapshot).count() == 2
