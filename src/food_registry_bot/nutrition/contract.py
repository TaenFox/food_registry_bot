from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class NutritionUnit(str, Enum):
    GRAM = "g"
    MILLILITER = "ml"


class NutritionConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


SUPPORTED_NUTRITION_METRIC_CODES = ("calories", "protein", "fat", "carbs", "fiber")


def _ensure_unique_values(values: list[str], *, error_message: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(error_message)


class NutritionEstimationItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_item_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    quantity: Optional[int] = Field(default=None, gt=0)
    unit: Optional[NutritionUnit] = None

    @field_validator("unit", mode="before")
    @classmethod
    def normalize_unit(cls, value: str | NutritionUnit | None) -> str | NutritionUnit | None:
        if value is None or isinstance(value, NutritionUnit):
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

    @model_validator(mode="after")
    def validate_quantity_unit_pair(self) -> "NutritionEstimationItemInput":
        if self.quantity is not None and self.unit is None:
            raise ValueError("quantity requires unit")
        if self.unit is not None and self.quantity is None:
            raise ValueError("unit requires quantity")
        return self


class NutritionEstimationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[NutritionEstimationItemInput] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_client_item_ids(self) -> "NutritionEstimationRequest":
        _ensure_unique_values(
            [item.client_item_id for item in self.items],
            error_message="client_item_id values must be unique",
        )
        return self


class NutritionMetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=64)
    value: float = Field(ge=0)
    confidence: NutritionConfidence

    @field_validator("code")
    @classmethod
    def validate_supported_metric_code(cls, value: str) -> str:
        if value not in SUPPORTED_NUTRITION_METRIC_CODES:
            raise ValueError(f"Unsupported nutrition metric code: {value}")
        return value


class NutritionEstimationItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_item_id: str = Field(min_length=1, max_length=128)
    metrics: List[NutritionMetricResult] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_required_metric_set(self) -> "NutritionEstimationItemResult":
        metric_codes = [metric.code for metric in self.metrics]
        _ensure_unique_values(metric_codes, error_message="metric code values must be unique within item")
        if set(metric_codes) != set(SUPPORTED_NUTRITION_METRIC_CODES):
            raise ValueError("each item must contain exactly calories, protein, fat, carbs, fiber metrics")
        return self


class NutritionEstimationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[NutritionEstimationItemResult] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_client_item_ids(self) -> "NutritionEstimationPayload":
        _ensure_unique_values(
            [item.client_item_id for item in self.items],
            error_message="client_item_id values must be unique",
        )
        return self
