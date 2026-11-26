from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal, Optional
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from sqlalchemy.orm import Session

from food_registry_bot.db.models import User
from food_registry_bot.db.repositories import (
    DEFAULT_DAILY_GOALS,
    DEFAULT_SUMMARY_PREFERENCES,
    GOAL_FIELDS,
    SUPPORTED_NUTRITION_DAY_START_HOURS,
    SUPPORTED_REPORT_GOAL_TOLERANCE_PERCENTS,
    SUPPORTED_REPORT_NOTICEABLE_ENTRY_PERCENTILES,
    SUPPORTED_SUMMARY_DISPLAY_MODES,
    USER_LLM_CONTEXT_COMMENT_MAX_LENGTH,
    SupportedDietRepository,
    UserDietPreferenceRepository,
    UserGoalPreferenceRepository,
    UserLLMProfileRepository,
    UserSummaryPreferenceRepository,
)

GoalMetric = Literal["calories", "protein", "fat", "carbs", "fiber", "water"]
GoalValue = Annotated[int, Field(gt=0, le=2_147_483_647)]
GOAL_STEPS = {"calories": 100, "protein": 5, "fat": 5, "carbs": 5, "fiber": 5, "water": 100}
SUMMARY_CHOICES = {
    "summary_display_mode": SUPPORTED_SUMMARY_DISPLAY_MODES,
    "nutrition_day_start_hour": SUPPORTED_NUTRITION_DAY_START_HOURS,
    "report_goal_tolerance_percent": SUPPORTED_REPORT_GOAL_TOLERANCE_PERCENTS,
    "report_noticeable_entry_percentile": SUPPORTED_REPORT_NOTICEABLE_ENTRY_PERCENTILES,
}


@lru_cache(maxsize=1)
def supported_timezones() -> tuple[str, ...]:
    return tuple(sorted(available_timezones()))


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    goals: dict[GoalMetric, GoalValue] = Field(default_factory=dict)
    metrics: dict[GoalMetric, bool] = Field(default_factory=dict)
    diets: dict[str, bool] = Field(default_factory=dict)
    workout_logging_enabled: Optional[bool] = None
    show_day_progress_bar: Optional[bool] = None
    show_post_entry_delta_suffix: Optional[bool] = None
    summary_display_mode: Optional[str] = None
    nutrition_day_start_hour: Optional[int] = None
    report_goal_tolerance_percent: Optional[int] = None
    report_noticeable_entry_percentile: Optional[int] = None
    timezone: Optional[str] = None
    user_context_comment: Optional[str] = Field(default=None, max_length=USER_LLM_CONTEXT_COMMENT_MAX_LENGTH)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is not None and value not in supported_timezones():
            raise ValueError("Unsupported time zone")
        return value

    @field_validator(*SUMMARY_CHOICES)
    @classmethod
    def validate_choice(cls, value: int | str | None, info: ValidationInfo) -> int | str | None:
        if value is not None and value not in SUMMARY_CHOICES[info.field_name]:
            raise ValueError("Unsupported settings value")
        return value


def read_settings(session: Session, user: User, *, context_encryption_secret: str | None = None) -> dict:
    goals = UserGoalPreferenceRepository(session).get_by_user_id(user.id)
    summary = UserSummaryPreferenceRepository(session).get_by_user_id(user.id)
    diets = UserDietPreferenceRepository(session).list_diets_for_user(user_id=user.id)
    preferences = {
        field: getattr(summary, field, default)
        for field, default in DEFAULT_SUMMARY_PREFERENCES.items()
    }
    context_available = bool(context_encryption_secret and context_encryption_secret.strip())
    context = ""
    if context_available:
        try:
            context = UserLLMProfileRepository(session).get_user_context_comment(
                user_id=user.id, encryption_secret=context_encryption_secret,
            ) or ""
        except ValueError:
            context_available = False
    return {
        "goals": {
            code: getattr(goals, field, DEFAULT_DAILY_GOALS[code])
            for code, field in GOAL_FIELDS.items()
        },
        "goal_steps": GOAL_STEPS,
        "metrics": {code: preferences.pop(f"show_{code}") for code in GOAL_FIELDS},
        "diets": [
            {"code": diet.code, "name": diet.name, "selected": diet.is_selected}
            for diet in diets
        ],
        "choices": {**SUMMARY_CHOICES, "timezone": supported_timezones()},
        "workout_logging_enabled": user.workout_logging_enabled,
        "timezone": user.timezone,
        "user_context_comment": context,
        "context_available": context_available,
        "context_max_length": USER_LLM_CONTEXT_COMMENT_MAX_LENGTH,
        **preferences,
    }


def apply_settings(
    session: Session, user: User, patch: SettingsPatch, *, context_encryption_secret: str | None = None,
) -> bool:
    diets = {}
    if patch.diets:
        diets = {diet.code: diet for diet in SupportedDietRepository(session).list_enabled()}
        if not patch.diets.keys() <= diets.keys():
            return False

    session.refresh(user, with_for_update={"key_share": True})
    if patch.workout_logging_enabled is not None:
        user.workout_logging_enabled = patch.workout_logging_enabled
    if patch.timezone is not None:
        user.timezone = patch.timezone
    if patch.user_context_comment is not None and context_encryption_secret:
        UserLLMProfileRepository(session).set_user_context_comment(
            user_id=user.id, user_context_comment=patch.user_context_comment.strip() or None,
            encryption_secret=context_encryption_secret,
        )

    if patch.goals:
        goals, _ = UserGoalPreferenceRepository(session).get_or_create(user_id=user.id)
        for code, value in patch.goals.items():
            setattr(goals, GOAL_FIELDS[code], value)

    summary_values = patch.model_dump(exclude_none=True, exclude={
        "goals", "metrics", "diets", "workout_logging_enabled", "timezone", "user_context_comment",
    })
    summary_values.update({f"show_{code}": value for code, value in patch.metrics.items()})
    if summary_values:
        summary, _ = UserSummaryPreferenceRepository(session).get_or_create(user_id=user.id)
        for field, value in summary_values.items():
            setattr(summary, field, value)

    diet_preferences = UserDietPreferenceRepository(session)
    for code, enabled in sorted(patch.diets.items()):
        diet_preferences.set_enabled(user_id=user.id, diet_id=diets[code].id, is_enabled=enabled)
    session.flush()
    return True
