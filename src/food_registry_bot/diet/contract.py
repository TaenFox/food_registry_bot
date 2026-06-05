from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


DietScoreConfidence = Literal["low", "medium", "high"]


class DietEvaluationItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_item_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=255)
    quantity: Optional[int] = Field(default=None, gt=0)
    unit: Optional[str] = Field(default=None, max_length=32)


class DietDefinitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    metric_code: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    scoring_guidance: str = Field(min_length=1)


class DietScoreResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    value: float = Field(ge=1, le=10)
    confidence: DietScoreConfidence


class DietEvaluationItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_item_id: str = Field(min_length=1)
    scores: list[DietScoreResult] = Field(min_length=1)


class DietEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diets: list[DietDefinitionInput] = Field(min_length=1)
    items: list[DietEvaluationItemInput] = Field(min_length=1)


class DietEvaluationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DietEvaluationItemResult] = Field(min_length=1)
