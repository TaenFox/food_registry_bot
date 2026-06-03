from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupportedDietDefinition:
    code: str
    name: str
    metric_code: str
    description: str
    scoring_guidance: str


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
            "Water should receive a very high score because hydration is beneficial for this diet. "
            "Very low scores usually apply to organ meats, sardines, anchovies, strong meat broths, and beer. "
            "Higher scores usually apply to water, eggs, most vegetables, fruits, grains, and moderate dairy."
        ),
    ),
}


def get_supported_diet_definition(code: str) -> SupportedDietDefinition:
    try:
        return SUPPORTED_DIET_DEFINITIONS[code]
    except KeyError as exc:
        raise ValueError(f"Unsupported diet code: {code}") from exc


def get_supported_diet_metric_codes() -> tuple[str, ...]:
    return tuple(definition.metric_code for definition in SUPPORTED_DIET_DEFINITIONS.values())
