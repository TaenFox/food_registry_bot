from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupportedDietDefinition:
    code: str
    name: str
    metric_code: str
    description: str
    scoring_guidance: str
    water_score: float | None = None


SUPPORTED_DIET_DEFINITIONS = {
    "low_purine": SupportedDietDefinition(
        code="low_purine",
        name="Низкопуриновая",
        metric_code="low_purine_score",
        description=(
            "Питание с приоритетом продуктов с низким содержанием пуринов и достаточной гидратацией."
        ),
        scoring_guidance=(
            "Score 1 means the item strongly conflicts with a low-purine diet. "
            "Score 5 is neutral or unclear. "
            "Score 10 means the item fits very well. "
            "Very low scores usually apply to organ meats, sardines, anchovies, strong meat broths, and beer. "
            "Higher scores usually apply to eggs, most vegetables, fruits, grains, and moderate dairy."
        ),
        water_score=8.0,
    ),
    "insulin_resistance": SupportedDietDefinition(
        code="insulin_resistance",
        name="При инсулинорезистентности",
        metric_code="insulin_resistance_score",
        description=(
            "Питание с приоритетом умеренной гликемической нагрузки, достаточного белка, "
            "клетчатки и менее обработанных источников углеводов."
        ),
        scoring_guidance=(
            "Score 1 means the item strongly conflicts with an insulin resistance friendly diet. "
            "Score 5 is neutral or unclear. "
            "Score 10 means the item fits very well. "
            "Very low scores usually apply to sugary drinks, desserts, large portions of refined carbs, "
            "and combinations with high sugar and low fiber. "
            "Higher scores usually apply to vegetables, legumes, eggs, fish, unsweetened dairy, "
            "whole grains in moderate portions, and meals with clear protein and fiber."
        ),
        water_score=10.0,
    ),
}


def get_supported_diet_definition(code: str) -> SupportedDietDefinition:
    try:
        return SUPPORTED_DIET_DEFINITIONS[code]
    except KeyError as exc:
        raise ValueError(f"Unsupported diet code: {code}") from exc


def get_supported_diet_metric_codes() -> tuple[str, ...]:
    return tuple(definition.metric_code for definition in SUPPORTED_DIET_DEFINITIONS.values())
