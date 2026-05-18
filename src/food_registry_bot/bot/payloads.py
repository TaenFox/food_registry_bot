from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from food_registry_bot.db.models import EntryType


class NormalizedEntryItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    quantity: Optional[int] = Field(default=None, ge=0)
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


class NormalizedEntryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: EntryType
    occurred_at: Optional[datetime] = None
    items: List[NormalizedEntryItemPayload] = Field(min_length=1)


class NormalizedJournalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: List[NormalizedEntryPayload] = Field(min_length=1)
