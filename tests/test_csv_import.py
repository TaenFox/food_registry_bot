from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import DailyGoalSnapshot, EntryItemMetric, EntryType, MealType
from food_registry_bot.db.repositories import UserRepository
from food_registry_bot.db.models import SupportedMetric
from food_registry_bot.importing.csv_import import (
    CSV_CONTRACT_TYPE_PARTIAL,
    CsvNutritionImporter,
    CsvWorkoutImporter,
    detect_csv_contract,
)
from food_registry_bot.nutrition import DailyNutritionSummaryUseCase, DailyWaterSummaryUseCase

FIXTURES_DIR = Path(__file__).parent / "fixtures"


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
            SupportedMetric(code="workout_calories", name="Workout Calories", unit="kcal"),
            SupportedMetric(code="workout_calorie_credit", name="Workout Calorie Credit", unit="kcal"),
        ]
    )
    session.commit()
    return session


def test_csv_import_parses_food_and_water_rows() -> None:
    csv_path = FIXTURES_DIR / "import_full.csv"
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
                (FIXTURES_DIR / "import_full.csv").read_text(encoding="utf-8").strip(),
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


def test_csv_import_accepts_partial_contract_and_keeps_only_provided_metrics(tmp_path: Path) -> None:
    csv_path = tmp_path / "partial_import.csv"
    csv_path.write_text((FIXTURES_DIR / "import_partial.csv").read_text(encoding="utf-8"), encoding="utf-8")
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4243, username="partial_import_user")

    read_result = CsvNutritionImporter.read_csv_with_metadata(csv_path)
    result = CsvNutritionImporter(session).import_rows(user=user, rows=read_result.rows)
    session.commit()

    assert read_result.contract_type == CSV_CONTRACT_TYPE_PARTIAL
    assert read_result.rows[0].provided_metric_codes == frozenset({"calories"})
    assert result.created_entry_count == 1

    assert session.query(DailyGoalSnapshot).count() == 1
    saved_metrics = session.query(EntryItemMetric).all()
    assert len(saved_metrics) == 1
    assert saved_metrics[0].metric.code == "calories"
    assert saved_metrics[0].value == 121.0


def test_workout_csv_import_parses_rows_and_imports_metrics() -> None:
    csv_path = FIXTURES_DIR / "import_workout.csv"
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4244, username="workout_import_user")

    read_result = CsvWorkoutImporter.read_csv_with_metadata(csv_path)
    result = CsvWorkoutImporter(session).import_rows(user=user, rows=read_result.rows)
    session.commit()

    assert read_result.contract_type == "workout_csv_v1"
    assert len(read_result.rows) == 2
    assert read_result.rows[0].duration_minutes == 92
    assert read_result.rows[0].workout_calories == 652.0
    assert read_result.rows[0].workout_calorie_credit == 250.0
    assert result.created_entry_count == 2

    saved_metrics = session.query(EntryItemMetric).all()
    assert len(saved_metrics) == 4


def test_detect_csv_contract_uses_headers_instead_of_filename(tmp_path: Path) -> None:
    csv_path = tmp_path / "Дневник_питания_Паша_Тренировки.csv"
    csv_path.write_text((FIXTURES_DIR / "import_full.csv").read_text(encoding="utf-8"), encoding="utf-8")

    detected_contract = detect_csv_contract(csv_path)

    assert detected_contract.contract_type == "food_registry_csv_v1"
