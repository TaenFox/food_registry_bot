from __future__ import annotations

from enum import Enum
from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NutritionUnit(str, Enum):
    GRAM = "g"
    MILLILITER = "ml"


def _ensure_unique_client_item_ids(values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError("client_item_id values must be unique")


class NutritionEstimationItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_item_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    quantity: int = Field(gt=0)
    unit: NutritionUnit

    @field_validator("unit", mode="before")
    @classmethod
    def normalize_unit(cls, value: str | NutritionUnit) -> str | NutritionUnit:
        if isinstance(value, NutritionUnit):
            return value

        normalized_value = value.strip().lower()
        unit_aliases = {
            "g": "g",
            "гр": "g",
            "г": "g",
            "ml": "ml",
            "мл": "ml",
        }
        return unit_aliases.get(normalized_value, normalized_value)


class NutritionEstimationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[NutritionEstimationItemInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_client_item_ids(self) -> "NutritionEstimationRequest":
        _ensure_unique_client_item_ids([item.client_item_id for item in self.items])
        return self


class NutritionEstimationItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_item_id: str = Field(min_length=1, max_length=128)
    calories: int = Field(ge=0)
    protein: float = Field(ge=0)
    fat: float = Field(ge=0)
    carbs: float = Field(ge=0)


class NutritionEstimationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[NutritionEstimationItemResult] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_client_item_ids(self) -> "NutritionEstimationPayload":
        _ensure_unique_client_item_ids([item.client_item_id for item in self.items])
        return self
