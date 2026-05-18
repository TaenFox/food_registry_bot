from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from food_registry_bot.db.models import EntryType


class ExtractedJournalItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    quantity: Optional[int] = Field(default=None, gt=0)
    unit: Optional[str] = Field(default=None, min_length=1, max_length=32)

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
        }
        return unit_aliases.get(normalized_value, normalized_value)

    @model_validator(mode="after")
    def validate_quantity_unit_pair(self) -> "ExtractedJournalItem":
        if self.unit is not None and self.quantity is None:
            raise ValueError("unit requires quantity")
        return self


class ExtractedJournalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: EntryType
    occurred_at: Optional[datetime] = None
    items: List[ExtractedJournalItem] = Field(min_length=1)


class ExtractedJournalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: List[ExtractedJournalEntry] = Field(min_length=1)
