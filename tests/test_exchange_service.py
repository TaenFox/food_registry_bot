from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.db.base import Base
from food_registry_bot.db.models import DataExchangeDirection, DataExchangeStatus, EntryItemMetric, SupportedMetric
from food_registry_bot.db.repositories import UserRepository
from food_registry_bot.exchange.service import (
    CSV_CONTRACT_TYPE_PARTIAL,
    DataExchangeService,
    DuplicateDataRowError,
    DuplicateFileError,
    FileLimitExceededError,
    LocalDataExchangeStorage,
    PartialImportRequiresAdminError,
)
from food_registry_bot.nutrition import StaticNutritionEstimationService


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


def write_csv(path: Path, rows: list[str]) -> None:
    path.write_text("\n".join(rows), encoding="utf-8")


def build_sample_csv(
    csv_path: Path,
    *,
    item_name: str = "Творог 5%",
    entry_date: str = "2026-05-20",
) -> None:
    write_csv(
        csv_path,
        [
            "Дата,Приём пищи,Блюдо / продукт,Категории,Количество,Единица,Ккал,\"Белки, г\",\"Жиры, г\",\"Углеводы, г\",\"Клетчатка, г\",Комментарий,Источник оценки,Уверенность оценки",
            f"{entry_date},Завтрак,{item_name},завтрак,100,г,\"121,0\",\"17,0\",\"5,0\",\"3,0\",\"0,0\",По справочнику,справочник,средняя",
            f"{entry_date},Напиток,Вода,\"напиток, гидратация\",250,мл,\"0,0\",\"0,0\",\"0,0\",\"0,0\",\"0,0\",Стакан воды,описание,высокая",
        ],
    )


def build_partial_csv(
    csv_path: Path,
    *,
    item_name: str = "Творог 5%",
    entry_date: str = "2026-05-20",
) -> None:
    write_csv(
        csv_path,
        [
            "Дата,Блюдо / продукт,Количество,Единица,Ккал",
            f"{entry_date},{item_name},100,г,\"121,0\"",
        ],
    )


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

    food_count, water_count = service.import_file(exchange_file=exchange_file, user=user)

    assert food_count == 1
    assert water_count == 1
    assert exchange_file.status is DataExchangeStatus.PROCESSED


def test_create_export_file_and_mark_processed(tmp_path: Path) -> None:
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

    export_file = service.create_export_file(user=user)
    export_path = service.get_download_path(exchange_file=export_file)

    assert export_file.direction is DataExchangeDirection.EXPORT
    assert export_file.status is DataExchangeStatus.READY
    assert export_path.exists()

    service.mark_export_downloaded(exchange_file=export_file)

    assert export_file.status is DataExchangeStatus.PROCESSED


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


def test_partial_import_file_is_saved_but_requires_admin_processing(tmp_path: Path) -> None:
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
    assert exchange_file.validation_message == "Файл требует обработки администратором."
    with pytest.raises(PartialImportRequiresAdminError):
        service.import_file(exchange_file=exchange_file, user=user)


def test_admin_process_import_file_fills_only_missing_metrics(tmp_path: Path) -> None:
    session = create_test_session()
    user = UserRepository(session).create(telegram_user_id=4008, username="partial_admin_user")
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

    nutrition_service = StaticNutritionEstimationService(
        raw_payload=(
            '{"items":[{"client_item_id":"entry-1:item-0","metrics":['
            '{"code":"calories","value":130.0,"confidence":"medium"},'
            '{"code":"protein","value":17.0,"confidence":"medium"},'
            '{"code":"fat","value":5.0,"confidence":"medium"},'
            '{"code":"carbs","value":3.0,"confidence":"medium"},'
            '{"code":"fiber","value":0.0,"confidence":"medium"}]}]}'
        )
    )

    food_count, water_count, estimated_metric_count = service.process_partial_import_file(
        exchange_file=exchange_file,
        nutrition_service=nutrition_service,
    )

    assert food_count == 1
    assert water_count == 0
    assert estimated_metric_count == 4
    assert exchange_file.status is DataExchangeStatus.PROCESSED

    saved_metrics = session.query(EntryItemMetric).all()
    assert len(saved_metrics) == 5
    calories_metric = next(metric for metric in saved_metrics if metric.metric.code == "calories")
    assert calories_metric.value == 121.0


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
