from __future__ import annotations

import csv
import contextlib
import hashlib
import io
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from food_registry_bot.config import get_data_exchange_dir
from food_registry_bot.db.models import (
    DataExchangeDirection,
    DataExchangeFile,
    DataExchangeStatus,
    EntryType,
    MealType,
    User,
)
from food_registry_bot.db.repositories import DataExchangeFileRepository, EntryRepository
from food_registry_bot.db.repositories import UserSummaryPreferenceRepository
from food_registry_bot.importing.csv_import import (
    CSV_CONTRACT_TYPE_FULL,
    CSV_CONTRACT_TYPE_WORKOUT,
    CsvNutritionImporter,
    CsvWorkoutImporter,
    ImportedNutritionRow,
    ImportedWorkoutRow,
    ROW_METRIC_CODES,
    build_workout_import_row_signature,
    detect_csv_contract,
    summarize_import_rows,
    summarize_workout_import_rows,
)
from food_registry_bot.nutrition import resolve_local_summary_date

IMPORT_FILE_LIMIT = 5
EXPORT_FILE_LIMIT = 5
CSV_HEADERS = [
    "Дата",
    "Приём пищи",
    "Блюдо / продукт",
    "Категории",
    "Количество",
    "Единица",
    "Ккал",
    "Белки, г",
    "Жиры, г",
    "Углеводы, г",
    "Клетчатка, г",
    "Комментарий",
    "Источник оценки",
    "Уверенность оценки",
]
WORKOUT_CSV_HEADERS = [
    "Дата",
    "Тип",
    "Длительность, мин",
    "Интенсивность",
    "Ккал тренировки",
    "Ккал учитывать в питании",
    "Учитывать",
    "Комментарий",
]
MEAL_TYPE_LABELS = {
    MealType.BREAKFAST: "Завтрак",
    MealType.LUNCH: "Обед",
    MealType.DINNER: "Ужин",
    MealType.SNACK: "Перекус",
    MealType.DRINK: "Напиток",
    None: "",
}


class UnsupportedExchangeFileError(ValueError):
    pass


class DuplicateFileError(ValueError):
    pass


@dataclass(frozen=True)
class DuplicateDataRowError(ValueError):
    row_number: int
    description: str

    def __str__(self) -> str:
        return self.description


class FileLimitExceededError(ValueError):
    pass


def _format_metric_value(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _format_row_description(row: ImportedNutritionRow) -> str:
    meal_label = MEAL_TYPE_LABELS.get(row.meal_type) or "Без типа"
    return f"{row.summary_date.isoformat()}, {meal_label}, {row.item_name}"


def _compute_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LocalDataExchangeStorage:
    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = Path(base_dir or get_data_exchange_dir())

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def ensure_base_dir(self) -> Path:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        return self._base_dir

    def build_relative_path(
        self,
        *,
        user_id: int,
        direction: DataExchangeDirection,
        original_filename: str,
        sha256: str,
    ) -> str:
        safe_filename = "".join(char if char.isalnum() or char in "._-" else "_" for char in original_filename)
        filename = f"{sha256[:12]}_{safe_filename or 'exchange.csv'}"
        return str(Path(f"user_{user_id}") / direction.value / filename)

    def write_copy(self, *, source_path: Path, relative_path: str) -> Path:
        target_path = self.ensure_base_dir() / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        return target_path

    def write_bytes(self, *, payload: bytes, relative_path: str) -> Path:
        target_path = self.ensure_base_dir() / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(payload)
        return target_path

    def resolve_path(self, relative_path: str) -> Path:
        return self.ensure_base_dir() / relative_path

    def delete(self, relative_path: str) -> None:
        target_path = self.resolve_path(relative_path)
        with contextlib.suppress(FileNotFoundError):
            target_path.unlink()
        parent = target_path.parent
        while parent != self._base_dir and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

@dataclass(frozen=True)
class ImportValidationResult:
    rows: list[ImportedNutritionRow] | list[ImportedWorkoutRow]
    contract_type: str
    row_count: int
    food_entry_count: int
    water_entry_count: int
    workout_entry_count: int
    date_from: date | None
    date_to: date | None


@dataclass(frozen=True)
class ImportExecutionResult:
    contract_type: str
    food_entry_count: int = 0
    water_entry_count: int = 0
    workout_entry_count: int = 0


@dataclass(frozen=True)
class ExportBuildResult:
    files: list[DataExchangeFile]


class DataExchangeService:
    def __init__(
        self,
        session: Session,
        *,
        storage: LocalDataExchangeStorage | None = None,
    ) -> None:
        self._session = session
        self._storage = storage or LocalDataExchangeStorage()
        self._file_repository = DataExchangeFileRepository(session)
        self._entry_repository = EntryRepository(session)
        self._summary_preference_repository = UserSummaryPreferenceRepository(session)

    def validate_import_file(
        self,
        *,
        user: User,
        source_path: Path,
        original_filename: str,
    ) -> tuple[str, ImportValidationResult]:
        if not original_filename.lower().endswith(".csv"):
            raise UnsupportedExchangeFileError(
                "Не знаю, как обработать этот файл. Ожидаю CSV в поддержанном обменном формате."
            )
        if self._file_repository.count_for_user_and_direction(
            user_id=user.id,
            direction=DataExchangeDirection.IMPORT,
        ) >= IMPORT_FILE_LIMIT:
            raise FileLimitExceededError(
                "Лимит импортных файлов достигнут. Удали лишний файл через /files и попробуй снова."
            )

        sha256 = _compute_sha256(source_path)
        existing_file = self._file_repository.get_by_sha256(
            user_id=user.id,
            direction=DataExchangeDirection.IMPORT,
            sha256=sha256,
        )
        if existing_file is not None:
            raise DuplicateFileError("Такой файл уже был загружен ранее.")

        try:
            detected_contract = detect_csv_contract(source_path)
        except ValueError as exc:
            raise UnsupportedExchangeFileError(
                "Не знаю, как обработать этот CSV-файл. Ожидаю поддержанный обменный файл food_registry_bot с данными по еде, воде или тренировкам."
            ) from exc

        if detected_contract.contract_type == CSV_CONTRACT_TYPE_WORKOUT:
            workout_read_result = CsvWorkoutImporter.read_csv_with_metadata(source_path)
            rows = workout_read_result.rows
            duplicate_row = self._find_duplicate_workout_row(user=user, rows=rows)
            if duplicate_row is not None:
                row_number, row = duplicate_row
                raise DuplicateDataRowError(
                    row_number=row_number,
                    description=(
                        "В файле обнаружены тренировки, которые уже есть в системе. "
                        f"Первый дубликат найден в строке {row_number}: {self._format_workout_row_description(row)}."
                    ),
                )
            summary = summarize_workout_import_rows(rows)
            read_contract_type = workout_read_result.contract_type
            food_entry_count = 0
            water_entry_count = 0
            workout_entry_count = summary.workout_entry_count
        else:
            nutrition_read_result = CsvNutritionImporter.read_csv_with_metadata(source_path)
            rows = nutrition_read_result.rows
            duplicate_row = self._find_duplicate_data_row(user=user, rows=rows)
            if duplicate_row is not None:
                row_number, row = duplicate_row
                raise DuplicateDataRowError(
                    row_number=row_number,
                    description=(
                        "В файле обнаружены данные, которые уже есть в системе. "
                        f"Первый дубликат найден в строке {row_number}: {_format_row_description(row)}."
                    ),
                )
            summary = summarize_import_rows(rows)
            read_contract_type = nutrition_read_result.contract_type
            food_entry_count = summary.food_entry_count
            water_entry_count = summary.water_entry_count
            workout_entry_count = 0

        return (
            sha256,
            ImportValidationResult(
                rows=rows,
                contract_type=read_contract_type,
                row_count=summary.row_count,
                food_entry_count=food_entry_count,
                water_entry_count=water_entry_count,
                workout_entry_count=workout_entry_count,
                date_from=summary.date_from,
                date_to=summary.date_to,
            ),
        )

    def create_import_file(
        self,
        *,
        user: User,
        source_path: Path,
        original_filename: str,
        sha256: str,
        validation_result: ImportValidationResult,
    ) -> DataExchangeFile:
        relative_path = self._storage.build_relative_path(
            user_id=user.id,
            direction=DataExchangeDirection.IMPORT,
            original_filename=original_filename,
            sha256=sha256,
        )
        self._storage.write_copy(source_path=source_path, relative_path=relative_path)
        return self._file_repository.create(
            user_id=user.id,
            direction=DataExchangeDirection.IMPORT,
            contract_type=validation_result.contract_type,
            original_filename=original_filename,
            storage_path=relative_path,
            sha256=sha256,
            row_count=validation_result.row_count,
            food_entry_count=validation_result.food_entry_count,
            water_entry_count=validation_result.water_entry_count,
            date_from=validation_result.date_from,
            date_to=validation_result.date_to,
            validation_message=(
                "Файл готов к импорту."
                if validation_result.contract_type in {CSV_CONTRACT_TYPE_FULL, CSV_CONTRACT_TYPE_WORKOUT}
                else "Файл готов к импорту. После импорта часть итогов может быть неполной."
            ),
        )

    def import_file(self, *, exchange_file: DataExchangeFile, user: User) -> ImportExecutionResult:
        if exchange_file.direction is not DataExchangeDirection.IMPORT:
            raise ValueError("Only import files can be imported")
        if exchange_file.status is DataExchangeStatus.PROCESSED:
            raise ValueError("Этот файл уже был импортирован ранее. Повторный импорт запрещён.")

        file_path = self._storage.resolve_path(exchange_file.storage_path)
        if exchange_file.contract_type == CSV_CONTRACT_TYPE_WORKOUT:
            rows = CsvWorkoutImporter.read_csv(file_path)
            duplicate_row = self._find_duplicate_workout_row(user=user, rows=rows)
            if duplicate_row is not None:
                row_number, row = duplicate_row
                raise DuplicateDataRowError(
                    row_number=row_number,
                    description=(
                        "Импорт остановлен: обнаружен дубликат уже существующих тренировок. "
                        f"Строка {row_number}: {self._format_workout_row_description(row)}."
                    ),
                )
            CsvWorkoutImporter(self._session).import_rows(user=user, rows=rows)
            self._file_repository.mark_processed(
                file_id=exchange_file.id,
                processing_message=f"Импорт выполнен: тренировок {len(rows)}.",
                processed_at=datetime.now(timezone.utc),
            )
            return ImportExecutionResult(
                contract_type=exchange_file.contract_type,
                workout_entry_count=len(rows),
            )

        rows = CsvNutritionImporter.read_csv(file_path)
        duplicate_row = self._find_duplicate_data_row(user=user, rows=rows)
        if duplicate_row is not None:
            row_number, row = duplicate_row
            raise DuplicateDataRowError(
                row_number=row_number,
                description=(
                    "Импорт остановлен: обнаружен дубликат уже существующих данных. "
                    f"Строка {row_number}: {_format_row_description(row)}."
                ),
            )

        CsvNutritionImporter(self._session).import_rows(user=user, rows=rows)
        imported_food_count = sum(1 for row in rows if row.entry_type is EntryType.FOOD)
        imported_water_count = sum(1 for row in rows if row.entry_type is EntryType.WATER)
        self._file_repository.mark_processed(
            file_id=exchange_file.id,
            processing_message=(
                f"Импорт выполнен: еда {imported_food_count}, вода {imported_water_count}."
            ),
            processed_at=datetime.now(timezone.utc),
        )
        return ImportExecutionResult(
            contract_type=exchange_file.contract_type,
            food_entry_count=imported_food_count,
            water_entry_count=imported_water_count,
        )

    def create_export_files(self, *, user: User) -> ExportBuildResult:
        nutrition_rows = self._build_nutrition_export_rows(user=user)
        workout_rows = self._build_workout_export_rows(user=user)
        export_specs: list[tuple[str, str, list[dict[str, str]], list[str]]] = []
        export_date = datetime.now(timezone.utc).date().isoformat()

        if nutrition_rows or not workout_rows:
            export_specs.append(
                (
                    CSV_CONTRACT_TYPE_FULL,
                    f"food_registry_export_{export_date}.csv",
                    nutrition_rows,
                    CSV_HEADERS,
                )
            )
        if workout_rows:
            export_specs.append(
                (
                    CSV_CONTRACT_TYPE_WORKOUT,
                    f"food_registry_workout_export_{export_date}.csv",
                    workout_rows,
                    WORKOUT_CSV_HEADERS,
                )
            )

        existing_export_count = self._file_repository.count_for_user_and_direction(
            user_id=user.id,
            direction=DataExchangeDirection.EXPORT,
        )
        if existing_export_count + len(export_specs) > EXPORT_FILE_LIMIT:
            raise FileLimitExceededError(
                "Лимит экспортных файлов достигнут. Удали лишний файл через /files и попробуй снова."
            )

        created_files: list[DataExchangeFile] = []
        for contract_type, filename, rows, headers in export_specs:
            content = self._render_csv(rows, fieldnames=headers)
            sha256 = hashlib.sha256(content).hexdigest()
            relative_path = self._storage.build_relative_path(
                user_id=user.id,
                direction=DataExchangeDirection.EXPORT,
                original_filename=filename,
                sha256=sha256,
            )
            self._storage.write_bytes(payload=content, relative_path=relative_path)
            created_files.append(
                self._create_export_file_record(
                    user=user,
                    contract_type=contract_type,
                    original_filename=filename,
                    relative_path=relative_path,
                    sha256=sha256,
                )
            )

        return ExportBuildResult(files=created_files)

    def mark_export_downloaded(self, *, exchange_file: DataExchangeFile) -> None:
        self._file_repository.mark_processed(
            file_id=exchange_file.id,
            processing_message="Файл отправлен в Telegram.",
            processed_at=datetime.now(timezone.utc),
        )

    def delete_file(self, *, exchange_file: DataExchangeFile) -> None:
        self._storage.delete(exchange_file.storage_path)
        self._file_repository.delete(exchange_file)

    def list_files(self, *, user_id: int) -> list[DataExchangeFile]:
        files = self._file_repository.list_for_user(user_id=user_id)

        def sort_key(item: DataExchangeFile) -> tuple[int, int, float]:
            processed_rank = 1 if item.status is DataExchangeStatus.PROCESSED else 0
            direction_rank = 0 if item.direction is DataExchangeDirection.IMPORT else 1
            created_timestamp = item.created_at.timestamp() if item.created_at.tzinfo else item.created_at.replace(tzinfo=timezone.utc).timestamp()
            return (processed_rank, direction_rank, -created_timestamp)

        return sorted(files, key=sort_key)

    def get_download_path(self, *, exchange_file: DataExchangeFile) -> Path:
        return self._storage.resolve_path(exchange_file.storage_path)

    def _find_duplicate_data_row(
        self,
        *,
        user: User,
        rows: list[ImportedNutritionRow],
    ) -> tuple[int, ImportedNutritionRow] | None:
        existing_rows = self._build_existing_entry_rows(user=user)
        for row in rows:
            for existing_row in existing_rows:
                if self._rows_match_for_duplicate(left=row, right=existing_row):
                    return row.row_number, row
        return None

    def _build_existing_entry_rows(self, *, user: User) -> list[ImportedNutritionRow]:
        entries = self._entry_repository.list_recent_for_user(user_id=user.id, limit=100000)
        summary_preference, _created = self._summary_preference_repository.get_or_create(user_id=user.id)
        existing_rows: list[ImportedNutritionRow] = []
        for entry in entries:
            if entry.entry_type not in {EntryType.FOOD, EntryType.WATER}:
                continue
            for item in entry.items:
                metrics_by_code = {
                    metric.metric.code: metric.value
                    for metric in item.metrics
                    if metric.metric is not None
                }
                imported_row = ImportedNutritionRow(
                    row_number=0,
                    summary_date=resolve_local_summary_date(
                        reference_at=entry.occurred_at,
                        timezone_name=user.timezone,
                        nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
                    ),
                    raw_meal_label=MEAL_TYPE_LABELS.get(entry.meal_type),
                    meal_type=entry.meal_type,
                    entry_type=entry.entry_type,
                    item_name=item.name,
                    quantity=item.quantity,
                    unit=item.unit,
                    metrics={
                        metric_code: metrics_by_code.get(metric_code, 0.0)
                        for metric_code in ROW_METRIC_CODES
                    },
                    provided_metric_codes=frozenset(metrics_by_code),
                    confidence=item.confidence or "medium",
                    source_type=item.source_type,
                    comment=entry.llm_comment,
                    raw_values={},
                )
                existing_rows.append(imported_row)
        return existing_rows

    def _find_duplicate_workout_row(
        self,
        *,
        user: User,
        rows: list[ImportedWorkoutRow],
    ) -> tuple[int, ImportedWorkoutRow] | None:
        existing_signatures = self._build_existing_workout_signatures(user=user)
        for row in rows:
            if build_workout_import_row_signature(row) in existing_signatures:
                return row.row_number, row
        return None

    def _build_existing_workout_signatures(self, *, user: User) -> set[str]:
        entries = self._entry_repository.list_recent_for_user(user_id=user.id, limit=100000)
        summary_preference, _created = self._summary_preference_repository.get_or_create(user_id=user.id)
        signatures: set[str] = set()
        for entry in entries:
            if entry.entry_type is not EntryType.WORKOUT:
                continue
            for item in entry.items:
                workout_calories = next(
                    (
                        metric.value
                        for metric in item.metrics
                        if metric.metric is not None and metric.metric.code == "workout_calories"
                    ),
                    0.0,
                )
                signatures.add(
                    build_workout_import_row_signature(
                        ImportedWorkoutRow(
                            row_number=0,
                            summary_date=resolve_local_summary_date(
                                reference_at=entry.occurred_at,
                                timezone_name=user.timezone,
                                nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
                            ),
                            workout_name=item.name,
                            duration_minutes=item.quantity or 0,
                            workout_calories=workout_calories,
                            workout_calorie_credit=0.0,
                            comment=entry.llm_comment,
                            raw_values={},
                        )
                    )
                )
        return signatures

    @staticmethod
    def _format_workout_row_description(row: ImportedWorkoutRow) -> str:
        return (
            f"{row.summary_date.isoformat()}, {row.workout_name}, "
            f"{row.duration_minutes} мин, {round(row.workout_calories, 1)} ккал"
        )

    @staticmethod
    def _rows_match_for_duplicate(
        *,
        left: ImportedNutritionRow,
        right: ImportedNutritionRow,
    ) -> bool:
        if left.summary_date != right.summary_date:
            return False
        if left.entry_type is not right.entry_type:
            return False
        if left.item_name.strip().lower() != right.item_name.strip().lower():
            return False
        if left.quantity != right.quantity:
            return False
        if (left.unit or "").strip().lower() != (right.unit or "").strip().lower():
            return False
        if left.meal_type is not None and right.meal_type is not None and left.meal_type is not right.meal_type:
            return False
        return True


    def _build_nutrition_export_rows(self, *, user: User) -> list[dict[str, str]]:
        entries = self._entry_repository.list_recent_for_user(user_id=user.id, limit=100000)
        summary_preference, _created = self._summary_preference_repository.get_or_create(user_id=user.id)
        rows: list[dict[str, str]] = []
        for entry in sorted(entries, key=lambda current: (current.occurred_at, current.id)):
            if entry.entry_type not in {EntryType.FOOD, EntryType.WATER}:
                continue
            for item in sorted(entry.items, key=lambda current: current.position):
                metrics_by_code = {
                    metric.metric.code: metric.value
                    for metric in item.metrics
                    if metric.metric is not None
                }
                is_water = entry.entry_type is EntryType.WATER
                rows.append(
                    {
                        "Дата": resolve_local_summary_date(
                            reference_at=entry.occurred_at,
                            timezone_name=user.timezone,
                            nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
                        ).isoformat(),
                        "Приём пищи": "Напиток" if is_water else (MEAL_TYPE_LABELS.get(entry.meal_type) or ""),
                        "Блюдо / продукт": item.name if not is_water else "water",
                        "Категории": "напиток, вода, гидратация" if is_water else "",
                        "Количество": str(item.quantity or ""),
                        "Единица": item.unit or "",
                        "Ккал": _format_metric_value(metrics_by_code.get("calories", 0.0)),
                        "Белки, г": _format_metric_value(metrics_by_code.get("protein", 0.0)),
                        "Жиры, г": _format_metric_value(metrics_by_code.get("fat", 0.0)),
                        "Углеводы, г": _format_metric_value(metrics_by_code.get("carbs", 0.0)),
                        "Клетчатка, г": _format_metric_value(metrics_by_code.get("fiber", 0.0)),
                        "Комментарий": entry.llm_comment or "",
                        "Источник оценки": item.source_type or "",
                        "Уверенность оценки": item.confidence or "",
                    }
                )
        return rows

    def _build_workout_export_rows(self, *, user: User) -> list[dict[str, str]]:
        entries = self._entry_repository.list_recent_for_user(user_id=user.id, limit=100000)
        summary_preference, _created = self._summary_preference_repository.get_or_create(user_id=user.id)
        rows: list[dict[str, str]] = []
        for entry in sorted(entries, key=lambda current: (current.occurred_at, current.id)):
            if entry.entry_type is not EntryType.WORKOUT:
                continue
            for item in sorted(entry.items, key=lambda current: current.position):
                metrics_by_code = {
                    metric.metric.code: metric.value
                    for metric in item.metrics
                    if metric.metric is not None
                }
                workout_credit = metrics_by_code.get("workout_calorie_credit", 0.0)
                rows.append(
                    {
                        "Дата": resolve_local_summary_date(
                            reference_at=entry.occurred_at,
                            timezone_name=user.timezone,
                            nutrition_day_start_hour=summary_preference.nutrition_day_start_hour,
                        ).isoformat(),
                        "Тип": item.name,
                        "Длительность, мин": str(item.quantity or ""),
                        "Интенсивность": "",
                        "Ккал тренировки": _format_metric_value(metrics_by_code.get("workout_calories", 0.0)),
                        "Ккал учитывать в питании": _format_metric_value(workout_credit),
                        "Учитывать": "да" if workout_credit > 0 else "нет",
                        "Комментарий": entry.llm_comment or "",
                    }
                )
        return rows

    def _create_export_file_record(
        self,
        *,
        user: User,
        contract_type: str,
        original_filename: str,
        relative_path: str,
        sha256: str,
    ) -> DataExchangeFile:
        resolved_path = self._storage.resolve_path(relative_path)
        if contract_type == CSV_CONTRACT_TYPE_WORKOUT:
            summary_rows = CsvWorkoutImporter.read_csv(resolved_path)
            summary = summarize_workout_import_rows(summary_rows)
            return self._file_repository.create(
                user_id=user.id,
                direction=DataExchangeDirection.EXPORT,
                contract_type=contract_type,
                original_filename=original_filename,
                storage_path=relative_path,
                sha256=sha256,
                row_count=summary.row_count,
                food_entry_count=0,
                water_entry_count=0,
                date_from=summary.date_from,
                date_to=summary.date_to,
                validation_message="Файл готов к скачиванию.",
            )

        summary_rows = CsvNutritionImporter.read_csv(resolved_path)
        summary = summarize_import_rows(summary_rows)
        return self._file_repository.create(
            user_id=user.id,
            direction=DataExchangeDirection.EXPORT,
            contract_type=contract_type,
            original_filename=original_filename,
            storage_path=relative_path,
            sha256=sha256,
            row_count=summary.row_count,
            food_entry_count=summary.food_entry_count,
            water_entry_count=summary.water_entry_count,
            date_from=summary.date_from,
            date_to=summary.date_to,
            validation_message="Файл готов к скачиванию.",
        )

    @staticmethod
    def _render_csv(rows: list[dict[str, str]], *, fieldnames: list[str]) -> bytes:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return buffer.getvalue().encode("utf-8")
