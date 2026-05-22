from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from food_registry_bot.db.models import EntryType, MealType, User
from food_registry_bot.db.repositories import (
    EntryItemCreate,
    EntryItemMetricRepository,
    EntryItemMetricValue,
    EntryRepository,
    UserSummaryPreferenceRepository,
)
from food_registry_bot.db.session import create_session_factory, session_scope
from food_registry_bot.nutrition import DailyNutritionGoalSnapshotUseCase

ROW_METRIC_CODES = ("calories", "protein", "fat", "carbs", "fiber")
MEAL_TYPE_BASE_TIMES = {
    MealType.BREAKFAST: (8, 0),
    MealType.LUNCH: (13, 0),
    MealType.DINNER: (19, 0),
    MealType.SNACK: (16, 0),
    MealType.DRINK: (10, 0),
    None: (12, 0),
}


@dataclass(frozen=True)
class ImportedNutritionRow:
    row_number: int
    summary_date: date
    raw_meal_label: str | None
    meal_type: MealType | None
    entry_type: EntryType
    item_name: str
    quantity: int | None
    unit: str | None
    metrics: dict[str, float]
    confidence: str
    source_type: str | None
    comment: str | None
    raw_values: dict[str, str]


@dataclass(frozen=True)
class CsvNutritionImportResult:
    imported_row_count: int
    created_entry_count: int
    created_snapshot_count: int


@dataclass(frozen=True)
class CsvNutritionRowSummary:
    row_count: int
    food_entry_count: int
    water_entry_count: int
    date_from: date | None
    date_to: date | None


def _normalize_header(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.strip().split())
    return cleaned or None


def _require_text(row: dict[str, str], header_map: dict[str, str], field_name: str) -> str:
    value = _clean_text(row.get(header_map[field_name]))
    if value is None:
        raise ValueError(f"Missing required field: {field_name}")
    return value


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_number(value: str | None) -> float | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    normalized = cleaned.replace(" ", "").replace(",", ".")
    return float(normalized)


def _parse_quantity(value: str | None) -> int | None:
    parsed = _parse_number(value)
    if parsed is None:
        return None
    if parsed.is_integer():
        return int(parsed)
    return None


def _normalize_confidence(value: str | None) -> str:
    cleaned = (_clean_text(value) or "").lower()
    mapping = {
        "низкая": "low",
        "low": "low",
        "средняя": "medium",
        "medium": "medium",
        "высокая": "high",
        "high": "high",
    }
    return mapping.get(cleaned, "medium")


def _normalize_source_type(value: str | None) -> str | None:
    cleaned = (_clean_text(value) or "").lower()
    mapping = {
        "фото": "photo",
        "справочник": "reference",
        "описание": "description",
    }
    return mapping.get(cleaned, cleaned or None)


def _normalize_item_name(value: str, *, entry_type: EntryType) -> str:
    cleaned = value.strip()
    if entry_type is EntryType.WATER:
        return "water"
    return cleaned


def build_import_row_signature(row: ImportedNutritionRow) -> str:
    metric_values = "|".join(f"{metric_code}={row.metrics[metric_code]:.4f}" for metric_code in ROW_METRIC_CODES)
    return "||".join(
        [
            row.summary_date.isoformat(),
            row.entry_type.value,
            row.meal_type.value if row.meal_type is not None else "",
            row.item_name.strip().lower(),
            str(row.quantity or ""),
            (row.unit or "").strip().lower(),
            metric_values,
        ]
    )


def summarize_import_rows(rows: list[ImportedNutritionRow]) -> CsvNutritionRowSummary:
    if not rows:
        return CsvNutritionRowSummary(
            row_count=0,
            food_entry_count=0,
            water_entry_count=0,
            date_from=None,
            date_to=None,
        )

    dates = [row.summary_date for row in rows]
    return CsvNutritionRowSummary(
        row_count=len(rows),
        food_entry_count=sum(1 for row in rows if row.entry_type is EntryType.FOOD),
        water_entry_count=sum(1 for row in rows if row.entry_type is EntryType.WATER),
        date_from=min(dates),
        date_to=max(dates),
    )


def _resolve_meal_type(value: str | None) -> MealType | None:
    cleaned = (_clean_text(value) or "").lower()
    mapping = {
        "завтрак": MealType.BREAKFAST,
        "breakfast": MealType.BREAKFAST,
        "обед": MealType.LUNCH,
        "lunch": MealType.LUNCH,
        "ужин": MealType.DINNER,
        "dinner": MealType.DINNER,
        "перекус": MealType.SNACK,
        "snack": MealType.SNACK,
        "напиток": MealType.DRINK,
        "drink": MealType.DRINK,
    }
    return mapping.get(cleaned)


def _resolve_entry_type(
    *,
    item_name: str,
    meal_type: MealType | None,
    categories: str | None,
    unit: str | None,
    calories: float,
) -> EntryType:
    normalized_name = item_name.strip().lower()
    normalized_categories = (categories or "").lower()
    if normalized_name == "вода" or normalized_name == "water":
        return EntryType.WATER
    if "гидратация" in normalized_categories and (unit or "").lower() == "мл" and calories == 0:
        return EntryType.WATER
    if meal_type is MealType.DRINK and normalized_name in {"вода", "water"}:
        return EntryType.WATER
    return EntryType.FOOD


def _resolve_occurred_at(
    *,
    summary_date: date,
    timezone_name: str,
    meal_type: MealType | None,
    meal_position: int,
) -> datetime:
    hour, minute = MEAL_TYPE_BASE_TIMES[meal_type]
    local_datetime = datetime(
        summary_date.year,
        summary_date.month,
        summary_date.day,
        hour,
        minute,
        tzinfo=ZoneInfo(timezone_name),
    ) + timedelta(minutes=meal_position)
    return local_datetime.astimezone(timezone.utc)


class CsvNutritionImporter:
    REQUIRED_HEADERS = {
        "date": "Дата",
        "meal": "Приём пищи",
        "item_name": "Блюдо / продукт",
        "categories": "Категории",
        "quantity": "Количество",
        "unit": "Единица",
        "calories": "Ккал",
        "protein": "Белки, г",
        "fat": "Жиры, г",
        "carbs": "Углеводы, г",
        "fiber": "Клетчатка, г",
        "comment": "Комментарий",
        "source": "Источник оценки",
        "confidence": "Уверенность оценки",
    }

    def __init__(self, session: Session) -> None:
        self._session = session
        self._entry_repository = EntryRepository(session)
        self._metric_repository = EntryItemMetricRepository(session)
        self._summary_repository = UserSummaryPreferenceRepository(session)
        self._goal_snapshot_use_case = DailyNutritionGoalSnapshotUseCase(session)

    def import_rows(
        self,
        *,
        user: User,
        rows: Iterable[ImportedNutritionRow],
    ) -> CsvNutritionImportResult:
        meal_counters: dict[tuple[date, MealType | None], int] = defaultdict(int)
        snapshot_dates: set[date] = set()
        created_entry_count = 0
        imported_row_count = 0

        summary_preferences, _created = self._summary_repository.get_or_create(user_id=user.id)
        for row in rows:
            meal_key = (row.summary_date, row.meal_type)
            occurred_at = _resolve_occurred_at(
                summary_date=row.summary_date,
                timezone_name=user.timezone,
                meal_type=row.meal_type,
                meal_position=meal_counters[meal_key],
            )
            meal_counters[meal_key] += 1

            entry = self._entry_repository.create(
                user_id=user.id,
                entry_type=row.entry_type,
                meal_type=None if row.entry_type is EntryType.WATER else row.meal_type,
                occurred_at=occurred_at,
                source_text=f"Импорт из CSV, строка {row.row_number}",
                extraction_provider="csv_import",
                extraction_raw_payload=json.dumps(row.raw_values, ensure_ascii=False, sort_keys=True),
                llm_comment=row.comment,
                items=[
                    EntryItemCreate(
                        name=row.item_name,
                        quantity=row.quantity,
                        unit=row.unit,
                        confidence=row.confidence,
                        source_type=row.source_type,
                    )
                ],
            )
            item = entry.items[0]
            if row.entry_type is EntryType.FOOD:
                self._metric_repository.upsert_metrics(
                    entry_item_id=item.id,
                    metric_values=[
                        EntryItemMetricValue(
                            code=metric_code,
                            value=row.metrics[metric_code],
                            confidence=row.confidence,
                        )
                        for metric_code in ROW_METRIC_CODES
                    ],
                )

            self._goal_snapshot_use_case.get_or_create(
                user_id=user.id,
                summary_date=row.summary_date,
                timezone_name=user.timezone,
                nutrition_day_start_hour=summary_preferences.nutrition_day_start_hour,
            )
            snapshot_dates.add(row.summary_date)
            created_entry_count += 1
            imported_row_count += 1

        return CsvNutritionImportResult(
            imported_row_count=imported_row_count,
            created_entry_count=created_entry_count,
            created_snapshot_count=len(snapshot_dates),
        )

    @classmethod
    def read_csv(cls, csv_path: Path) -> list[ImportedNutritionRow]:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("CSV file does not contain a header row")

            header_map = cls._resolve_header_map(reader.fieldnames)
            rows: list[ImportedNutritionRow] = []
            for row_number, row in enumerate(reader, start=2):
                if cls._is_empty_row(row):
                    continue
                imported_row = cls._parse_row(row_number=row_number, row=row, header_map=header_map)
                rows.append(imported_row)
        return rows

    @classmethod
    def _resolve_header_map(cls, headers: list[str]) -> dict[str, str]:
        normalized_headers = {_normalize_header(header): header for header in headers}
        header_map: dict[str, str] = {}
        missing_headers: list[str] = []
        for field_name, expected_header in cls.REQUIRED_HEADERS.items():
            actual_header = normalized_headers.get(_normalize_header(expected_header))
            if actual_header is None:
                missing_headers.append(expected_header)
                continue
            header_map[field_name] = actual_header

        if missing_headers:
            raise ValueError(f"Missing required CSV headers: {', '.join(missing_headers)}")
        return header_map

    @staticmethod
    def _is_empty_row(row: dict[str, str]) -> bool:
        return all(_clean_text(value) is None for value in row.values())

    @classmethod
    def _parse_row(
        cls,
        *,
        row_number: int,
        row: dict[str, str],
        header_map: dict[str, str],
    ) -> ImportedNutritionRow:
        raw_values = {
            field_name: row.get(actual_header, "")
            for field_name, actual_header in header_map.items()
        }
        summary_date = _parse_date(_require_text(row, header_map, "date"))
        raw_meal_label = _clean_text(row.get(header_map["meal"]))
        meal_type = _resolve_meal_type(raw_meal_label)
        raw_item_name = _require_text(row, header_map, "item_name")
        categories = _clean_text(row.get(header_map["categories"]))
        quantity = _parse_quantity(row.get(header_map["quantity"]))
        unit = _clean_text(row.get(header_map["unit"]))
        metrics = {
            metric_code: _parse_number(row.get(header_map[metric_code])) or 0.0
            for metric_code in ROW_METRIC_CODES
        }
        entry_type = _resolve_entry_type(
            item_name=raw_item_name,
            meal_type=meal_type,
            categories=categories,
            unit=unit,
            calories=metrics["calories"],
        )
        normalized_unit = "ml" if entry_type is EntryType.WATER else unit
        return ImportedNutritionRow(
            row_number=row_number,
            summary_date=summary_date,
            raw_meal_label=raw_meal_label,
            meal_type=meal_type,
            entry_type=entry_type,
            item_name=_normalize_item_name(raw_item_name, entry_type=entry_type),
            quantity=quantity,
            unit=normalized_unit,
            metrics=metrics,
            confidence=_normalize_confidence(row.get(header_map["confidence"])),
            source_type=_normalize_source_type(row.get(header_map["source"])),
            comment=_clean_text(row.get(header_map["comment"])),
            raw_values=raw_values,
        )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import nutrition history from a CSV export of Google Sheets."
    )
    parser.add_argument("csv_path", type=Path, help="Path to CSV export")
    parser.add_argument("telegram_user_id", type=int, help="Target Telegram user id")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override SQLAlchemy database URL",
    )
    return parser


def main() -> None:
    args = _build_argument_parser().parse_args()
    rows = CsvNutritionImporter.read_csv(args.csv_path)
    session_factory = create_session_factory(database_url=args.database_url)
    with session_scope(session_factory) as session:
        user = session.query(User).filter_by(telegram_user_id=args.telegram_user_id).one_or_none()
        if user is None:
            raise ValueError(f"User with telegram_user_id={args.telegram_user_id} was not found")

        result = CsvNutritionImporter(session).import_rows(user=user, rows=rows)

    print(
        json.dumps(
            {
                "imported_row_count": result.imported_row_count,
                "created_entry_count": result.created_entry_count,
                "created_snapshot_count": result.created_snapshot_count,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
