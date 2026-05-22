from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import DataExchangeDirection, DataExchangeStatus, SupportedMetric
from food_registry_bot.db.repositories import UserRepository
from food_registry_bot.exchange.service import (
    DataExchangeService,
    DuplicateDataRowError,
    DuplicateFileError,
    FileLimitExceededError,
    LocalDataExchangeStorage,
)
from food_registry_bot.importing.csv_import import (
    CSV_CONTRACT_TYPE_FULL,
    CSV_CONTRACT_TYPE_PARTIAL,
    CSV_CONTRACT_TYPE_WORKOUT,
)

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


def write_csv(path: Path, rows: list[str]) -> None:
    path.write_text("\n".join(rows), encoding="utf-8")


def build_sample_csv(
    csv_path: Path,
    *,
    item_name: str = "Творог 5%",
    entry_date: str = "2026-05-20",
) -> None:
    content = (FIXTURES_DIR / "import_full.csv").read_text(encoding="utf-8")
    content = content.replace("2026-05-20,Завтрак,Творог 5%", f"{entry_date},Завтрак,{item_name}")
    content = content.replace("2026-05-20,Напиток,Вода", f"{entry_date},Напиток,Вода")
    csv_path.write_text(content, encoding="utf-8")


def build_partial_csv(
    csv_path: Path,
    *,
    item_name: str = "Творог 5%",
    entry_date: str = "2026-05-20",
) -> None:
    content = (FIXTURES_DIR / "import_partial.csv").read_text(encoding="utf-8")
    content = content.replace("2026-05-20,Творог 5%", f"{entry_date},{item_name}")
    csv_path.write_text(content, encoding="utf-8")


def build_workout_csv(
    csv_path: Path,
    *,
    workout_name: str = "силовая: спина и руки",
    entry_date: str = "2026-05-20",
) -> None:
    content = (FIXTURES_DIR / "import_workout.csv").read_text(encoding="utf-8")
    content = content.replace("2026-05-20,силовая: спина и руки", f"{entry_date},{workout_name}")
    csv_path.write_text(content, encoding="utf-8")


def test_validate_and_store_import_file(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4001, username="exchange_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    csv_path = tmp_path / "import.csv"
    build_sample_csv(csv_path)

    service = DataExchangeService(session, storage=storage)
    sha256, validation_result = service.validate_import_file(
        user=user,
        source_path=csv_path,
        original_filename="import.csv",
    )
    exchange_file = service.create_import_file(
        user=user,
        source_path=csv_path,
        original_filename="import.csv",
        sha256=sha256,
        validation_result=validation_result,
    )

    assert exchange_file.direction is DataExchangeDirection.IMPORT
    assert exchange_file.status is DataExchangeStatus.READY
    assert exchange_file.food_entry_count == 1
    assert exchange_file.water_entry_count == 1
    assert storage.resolve_path(exchange_file.storage_path).exists()


def test_validate_import_rejects_exact_duplicate_file(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4002, username="duplicate_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    csv_path = tmp_path / "import.csv"
    build_sample_csv(csv_path)
    service = DataExchangeService(session, storage=storage)

    sha256, validation_result = service.validate_import_file(
        user=user,
        source_path=csv_path,
        original_filename="import.csv",
    )
    service.create_import_file(
        user=user,
        source_path=csv_path,
        original_filename="import.csv",
        sha256=sha256,
        validation_result=validation_result,
    )

    with pytest.raises(DuplicateFileError):
        service.validate_import_file(
            user=user,
            source_path=csv_path,
            original_filename="import.csv",
        )


def test_validate_import_rejects_duplicate_data_row_after_import(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4003, username="duplicate_data_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    csv_path = tmp_path / "import.csv"
    build_sample_csv(csv_path)
    service = DataExchangeService(session, storage=storage)

    sha256, validation_result = service.validate_import_file(
        user=user,
        source_path=csv_path,
        original_filename="import.csv",
    )
    exchange_file = service.create_import_file(
        user=user,
        source_path=csv_path,
        original_filename="import.csv",
        sha256=sha256,
        validation_result=validation_result,
    )
    service.import_file(exchange_file=exchange_file, user=user)
    session.commit()

    second_csv_path = tmp_path / "import_again.csv"
    write_csv(
        second_csv_path,
        [
            "Дата,Приём пищи,Блюдо / продукт,Категории,Количество,Единица,Ккал,\"Белки, г\",\"Жиры, г\",\"Углеводы, г\",\"Клетчатка, г\",Комментарий,Источник оценки,Уверенность оценки",
            "2026-05-20,Завтрак,Творог 5%,завтрак,100,г,\"121,0\",\"17,0\",\"5,0\",\"3,0\",\"0,0\",Другой комментарий,справочник,средняя",
            "2026-05-20,Напиток,Вода,\"напиток, гидратация\",250,мл,\"0,0\",\"0,0\",\"0,0\",\"0,0\",\"0,0\",Стакан воды,описание,высокая",
        ],
    )

    with pytest.raises(DuplicateDataRowError) as exc_info:
        service.validate_import_file(
            user=user,
            source_path=second_csv_path,
            original_filename="import_again.csv",
        )

    assert exc_info.value.row_number == 2


def test_import_marks_file_processed(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4004, username="import_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    csv_path = tmp_path / "import.csv"
    build_sample_csv(csv_path)
    service = DataExchangeService(session, storage=storage)

    sha256, validation_result = service.validate_import_file(
        user=user,
        source_path=csv_path,
        original_filename="import.csv",
    )
    exchange_file = service.create_import_file(
        user=user,
        source_path=csv_path,
        original_filename="import.csv",
        sha256=sha256,
        validation_result=validation_result,
    )

    import_result = service.import_file(exchange_file=exchange_file, user=user)

    assert import_result.food_entry_count == 1
    assert import_result.water_entry_count == 1
    assert exchange_file.status is DataExchangeStatus.PROCESSED


def test_create_export_files_and_mark_processed(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4005, username="export_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    csv_path = tmp_path / "import.csv"
    build_sample_csv(csv_path)
    service = DataExchangeService(session, storage=storage)

    sha256, validation_result = service.validate_import_file(
        user=user,
        source_path=csv_path,
        original_filename="import.csv",
    )
    exchange_file = service.create_import_file(
        user=user,
        source_path=csv_path,
        original_filename="import.csv",
        sha256=sha256,
        validation_result=validation_result,
    )
    service.import_file(exchange_file=exchange_file, user=user)

    export_result = service.create_export_files(user=user)
    assert len(export_result.files) == 1
    export_file = export_result.files[0]
    export_path = service.get_download_path(exchange_file=export_file)

    assert export_file.direction is DataExchangeDirection.EXPORT
    assert export_file.status is DataExchangeStatus.READY
    assert export_file.contract_type == CSV_CONTRACT_TYPE_FULL
    assert export_path.exists()

    service.mark_export_downloaded(exchange_file=export_file)

    assert export_file.status is DataExchangeStatus.PROCESSED


def test_create_export_files_includes_separate_workout_csv(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=40051, username="export_workout_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    nutrition_csv_path = tmp_path / "import.csv"
    workout_csv_path = tmp_path / "workout.csv"
    build_sample_csv(nutrition_csv_path)
    build_workout_csv(workout_csv_path)
    service = DataExchangeService(session, storage=storage)

    nutrition_sha256, nutrition_validation = service.validate_import_file(
        user=user,
        source_path=nutrition_csv_path,
        original_filename="import.csv",
    )
    nutrition_file = service.create_import_file(
        user=user,
        source_path=nutrition_csv_path,
        original_filename="import.csv",
        sha256=nutrition_sha256,
        validation_result=nutrition_validation,
    )
    service.import_file(exchange_file=nutrition_file, user=user)

    workout_sha256, workout_validation = service.validate_import_file(
        user=user,
        source_path=workout_csv_path,
        original_filename="workout.csv",
    )
    workout_file = service.create_import_file(
        user=user,
        source_path=workout_csv_path,
        original_filename="workout.csv",
        sha256=workout_sha256,
        validation_result=workout_validation,
    )
    service.import_file(exchange_file=workout_file, user=user)

    export_result = service.create_export_files(user=user)

    assert len(export_result.files) == 2
    assert {export_file.contract_type for export_file in export_result.files} == {
        CSV_CONTRACT_TYPE_FULL,
        CSV_CONTRACT_TYPE_WORKOUT,
    }
    workout_export_file = next(
        export_file for export_file in export_result.files if export_file.contract_type == CSV_CONTRACT_TYPE_WORKOUT
    )
    workout_export_path = service.get_download_path(exchange_file=workout_export_file)

    assert workout_export_file.row_count == 2
    assert "food_registry_workout_export_" in workout_export_file.original_filename
    assert workout_export_path.exists()


def test_limit_is_enforced_for_import_files(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4006, username="limited_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    service = DataExchangeService(session, storage=storage)

    for index in range(5):
        csv_path = tmp_path / f"import_{index}.csv"
        build_sample_csv(
            csv_path,
            item_name=f"Творог {index}",
            entry_date=f"2026-05-{20 + index:02d}",
        )
        sha256, validation_result = service.validate_import_file(
            user=user,
            source_path=csv_path,
            original_filename=f"import_{index}.csv",
        )
        service.create_import_file(
            user=user,
            source_path=csv_path,
            original_filename=f"import_{index}.csv",
            sha256=sha256,
            validation_result=validation_result,
        )

    sixth_csv_path = tmp_path / "import_5.csv"
    build_sample_csv(
        sixth_csv_path,
        item_name="Творог 5",
        entry_date="2026-05-31",
    )
    with pytest.raises(FileLimitExceededError):
        service.validate_import_file(
            user=user,
            source_path=sixth_csv_path,
            original_filename="import_5.csv",
        )


def test_partial_import_file_is_saved_and_can_be_imported_by_user(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4007, username="partial_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    csv_path = tmp_path / "partial.csv"
    build_partial_csv(csv_path)
    service = DataExchangeService(session, storage=storage)

    sha256, validation_result = service.validate_import_file(
        user=user,
        source_path=csv_path,
        original_filename="partial.csv",
    )
    exchange_file = service.create_import_file(
        user=user,
        source_path=csv_path,
        original_filename="partial.csv",
        sha256=sha256,
        validation_result=validation_result,
    )

    assert validation_result.contract_type == CSV_CONTRACT_TYPE_PARTIAL
    assert exchange_file.validation_message == "Файл готов к импорту. После импорта часть итогов может быть неполной."
    import_result = service.import_file(exchange_file=exchange_file, user=user)
    assert import_result.food_entry_count == 1
    assert import_result.water_entry_count == 0
    assert exchange_file.status is DataExchangeStatus.PROCESSED


def test_duplicate_check_uses_core_row_data_for_partial_file(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4009, username="duplicate_partial_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    strict_csv_path = tmp_path / "strict.csv"
    build_sample_csv(strict_csv_path)
    service = DataExchangeService(session, storage=storage)

    sha256, validation_result = service.validate_import_file(
        user=user,
        source_path=strict_csv_path,
        original_filename="strict.csv",
    )
    exchange_file = service.create_import_file(
        user=user,
        source_path=strict_csv_path,
        original_filename="strict.csv",
        sha256=sha256,
        validation_result=validation_result,
    )
    service.import_file(exchange_file=exchange_file, user=user)
    session.commit()

    partial_csv_path = tmp_path / "partial.csv"
    build_partial_csv(partial_csv_path)

    with pytest.raises(DuplicateDataRowError) as exc_info:
        service.validate_import_file(
            user=user,
            source_path=partial_csv_path,
            original_filename="partial.csv",
        )

    assert exc_info.value.row_number == 2


def test_validate_and_import_workout_file(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4010, username="workout_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    csv_path = tmp_path / "workout.csv"
    build_workout_csv(csv_path)
    service = DataExchangeService(session, storage=storage)

    sha256, validation_result = service.validate_import_file(
        user=user,
        source_path=csv_path,
        original_filename="workout.csv",
    )
    exchange_file = service.create_import_file(
        user=user,
        source_path=csv_path,
        original_filename="workout.csv",
        sha256=sha256,
        validation_result=validation_result,
    )
    import_result = service.import_file(exchange_file=exchange_file, user=user)

    assert validation_result.contract_type == CSV_CONTRACT_TYPE_WORKOUT
    assert validation_result.workout_entry_count == 2
    assert import_result.workout_entry_count == 2
    assert exchange_file.status is DataExchangeStatus.PROCESSED


def test_validate_import_detects_contract_by_headers_not_filename(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4012, username="header_detection_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    csv_path = tmp_path / "Дневник_питания_Паша_Тренировки.csv"
    build_sample_csv(csv_path)
    service = DataExchangeService(session, storage=storage)

    _sha256, validation_result = service.validate_import_file(
        user=user,
        source_path=csv_path,
        original_filename=csv_path.name,
    )

    assert validation_result.contract_type == CSV_CONTRACT_TYPE_FULL


def test_validate_workout_import_rejects_duplicate_after_import(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4011, username="workout_duplicate_user")
    storage = LocalDataExchangeStorage(base_dir=tmp_path / "data_exchange")
    csv_path = tmp_path / "workout.csv"
    build_workout_csv(csv_path)
    service = DataExchangeService(session, storage=storage)

    sha256, validation_result = service.validate_import_file(
        user=user,
        source_path=csv_path,
        original_filename="workout.csv",
    )
    exchange_file = service.create_import_file(
        user=user,
        source_path=csv_path,
        original_filename="workout.csv",
        sha256=sha256,
        validation_result=validation_result,
    )
    service.import_file(exchange_file=exchange_file, user=user)
    session.commit()

    duplicate_csv_path = tmp_path / "workout_duplicate.csv"
    build_workout_csv(
        duplicate_csv_path,
        workout_name="силовая: спина и руки",
        entry_date="2026-05-20",
    )
    duplicate_csv_path.write_text(
        duplicate_csv_path.read_text(encoding="utf-8").replace(
            "Основным источником считала часы",
            "Другой комментарий",
        ),
        encoding="utf-8",
    )

    with pytest.raises(DuplicateDataRowError) as exc_info:
        service.validate_import_file(
            user=user,
            source_path=duplicate_csv_path,
            original_filename="workout_duplicate.csv",
        )

    assert exc_info.value.row_number == 2
