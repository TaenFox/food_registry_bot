from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from food_registry_bot.diet.registry import SUPPORTED_DIET_DEFINITIONS
from food_registry_bot.nutrition.daily_summary import resolve_local_summary_date


class DietScoreSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    metric_code: str = Field(min_length=1)
    average_score: float = Field(ge=0)
    item_count: int = Field(ge=0)
    day_count: int = Field(ge=0)
    has_missing_scores: bool = False


def summarize_diet_scores(
    *,
    entries: Iterable,
    timezone_name: str,
    nutrition_day_start_hour: int,
) -> list[DietScoreSummary]:
    states = {
        definition.code: {
            "definition": definition,
            "total_score": 0.0,
            "item_count": 0,
            "summary_dates": set(),
            "has_missing_scores": False,
        }
        for definition in SUPPORTED_DIET_DEFINITIONS.values()
    }

    for entry in entries:
        if not getattr(entry, "items", None):
            continue

        summary_date = resolve_local_summary_date(
            reference_at=entry.occurred_at,
            timezone_name=timezone_name,
            nutrition_day_start_hour=nutrition_day_start_hour,
        )
        for item in entry.items:
            metric_values = {
                metric.metric.code: metric.value
                for metric in getattr(item, "metrics", [])
                if metric.metric is not None
            }
            for definition in SUPPORTED_DIET_DEFINITIONS.values():
                state = states[definition.code]
                score = metric_values.get(definition.metric_code)
                if score is None:
                    state["has_missing_scores"] = True
                    continue
                state["total_score"] += float(score)
                state["item_count"] += 1
                state["summary_dates"].add(summary_date)

    summaries: list[DietScoreSummary] = []
    for definition in SUPPORTED_DIET_DEFINITIONS.values():
        state = states[definition.code]
        item_count = state["item_count"]
        if item_count <= 0:
            continue
        summaries.append(
            DietScoreSummary(
                code=definition.code,
                name=definition.name,
                metric_code=definition.metric_code,
                average_score=round(state["total_score"] / item_count, 1),
                item_count=item_count,
                day_count=len(state["summary_dates"]),
                has_missing_scores=bool(state["has_missing_scores"]),
            )
        )
    return summaries
