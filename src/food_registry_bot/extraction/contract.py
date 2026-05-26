from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from food_registry_bot.db.models import EntryType
from food_registry_bot.nutrition_label import NutritionLabelData


class ExtractedJournalItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    quantity: Optional[int] = Field(default=None, gt=0)
    unit: Optional[str] = Field(default=None, min_length=1, max_length=32)
    metrics: List["ExtractedJournalMetric"] = Field(default_factory=list)
    nutrition_label: Optional[NutritionLabelData] = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized_value = value.strip()
        if normalized_value.lower() in {"water", "вода"}:
            return "water"
        return normalized_value

    @field_validator("unit")
    @classmethod
    def normalize_unit(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        normalized_value = value.strip().lower()
        unit_aliases = {
            "ml": "ml",
            "мл": "ml",
            "g": "g",
            "гр": "g",
            "г": "g",
            "min": "min",
            "minute": "min",
            "minutes": "min",
            "мин": "min",
            "минута": "min",
            "минуты": "min",
            "минут": "min",
        }
        return unit_aliases.get(normalized_value, normalized_value)

    @model_validator(mode="after")
    def validate_quantity_unit_pair(self) -> "ExtractedJournalItem":
        if self.quantity is not None and self.unit is None:
            raise ValueError("quantity requires unit")
        if self.unit is not None and self.quantity is None:
            raise ValueError("unit requires quantity")
        return self


class ExtractedJournalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: EntryType
    occurred_at: Optional[datetime] = None
    items: List[ExtractedJournalItem] = Field(min_length=1)

    @model_validator(mode="after")
    def normalize_water_items(self) -> "ExtractedJournalEntry":
        if self.type is not EntryType.WATER:
            return self

        for item in self.items:
            normalized_name = item.name.strip().lower()
            if normalized_name == "water" or "вод" in normalized_name:
                item.name = "water"

        return self

    @model_validator(mode="after")
    def validate_units_for_entry_type(self) -> "ExtractedJournalEntry":
        allowed_units_by_type = {
            EntryType.FOOD: {"g", "ml", None},
            EntryType.WATER: {"ml", None},
            EntryType.WORKOUT: {"min", None},
        }
        allowed_units = allowed_units_by_type.get(self.type)
        if allowed_units is None:
            return self

        for item in self.items:
            if item.unit not in allowed_units:
                raise ValueError(f"unit {item.unit!r} is not allowed for entry type {self.type.value}")
            if self.type is not EntryType.WORKOUT and item.metrics:
                raise ValueError(f"metrics are not allowed for entry type {self.type.value}")
            if self.type is not EntryType.FOOD and item.nutrition_label is not None:
                raise ValueError(f"nutrition_label is not allowed for entry type {self.type.value}")
            if self.type is EntryType.WORKOUT:
                for metric in item.metrics:
                    if metric.code != "workout_calories":
                        raise ValueError(f"metric {metric.code!r} is not allowed for workout entry")

        return self


class ExtractedJournalMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=64)
    value: float = Field(gt=0)
    confidence: str = Field(default="medium", min_length=1, max_length=32)


class ExtractedJournalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: List[ExtractedJournalEntry] = Field(min_length=1)
