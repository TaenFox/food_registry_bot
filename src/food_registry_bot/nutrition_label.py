from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NutritionLabelBasis(str, Enum):
    PER_100G = "per_100g"
    PER_100ML = "per_100ml"
    PER_SERVING = "per_serving"
    UNKNOWN = "unknown"


class NutritionLabelData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    basis: NutritionLabelBasis
    serving_quantity: Optional[int] = Field(default=None, gt=0)
    serving_unit: Optional[str] = Field(default=None, min_length=1, max_length=32)
    calories: float = Field(ge=0)
    protein: float = Field(ge=0)
    fat: float = Field(ge=0)
    carbs: float = Field(ge=0)
    fiber: Optional[float] = Field(default=None, ge=0)

    @field_validator("serving_unit")
    @classmethod
    def normalize_serving_unit(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        normalized_value = value.strip().lower()
        unit_aliases = {
            "g": "g",
            "гр": "g",
            "г": "g",
            "ml": "ml",
            "мл": "ml",
        }
        return unit_aliases.get(normalized_value, normalized_value)

    @model_validator(mode="after")
    def validate_basis_details(self) -> "NutritionLabelData":
        if self.basis is NutritionLabelBasis.PER_SERVING:
            if self.serving_quantity is None or self.serving_unit is None:
                raise ValueError("per_serving nutrition label requires serving_quantity and serving_unit")
            return self

        if self.serving_quantity is not None or self.serving_unit is not None:
            raise ValueError("serving_quantity and serving_unit are allowed only for per_serving nutrition label")

        return self
