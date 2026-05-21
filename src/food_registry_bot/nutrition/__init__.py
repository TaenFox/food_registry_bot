from food_registry_bot.nutrition.contract import (
    NutritionConfidence,
    NutritionEstimationItemInput,
    NutritionEstimationItemResult,
    NutritionMetricResult,
    NutritionEstimationPayload,
    NutritionEstimationRequest,
    SUPPORTED_NUTRITION_METRIC_CODES,
    NutritionUnit,
)
from food_registry_bot.nutrition.factory import create_nutrition_service
from food_registry_bot.nutrition.journal_adapter import (
    PreparedNutritionRequest,
    ResolvedNutritionEstimate,
    NutritionJournalItemRef,
    prepare_nutrition_request_from_entries,
    prepare_nutrition_request_from_extracted_payload,
    resolve_nutrition_estimates,
)
from food_registry_bot.nutrition.llm_client import LLMNutritionClient, LLMNutritionClientError
from food_registry_bot.nutrition.openai_client import OpenAIResponsesNutritionClient
from food_registry_bot.nutrition.service import (
    InvalidNutritionPayload,
    LLMNutritionEstimationService,
    NutritionEstimationService,
    NutritionPayloadClient,
    StaticNutritionEstimationService,
    ValidNutritionPayload,
)
from food_registry_bot.nutrition.use_cases import (
    BackfillNutritionEstimationUseCase,
    FailedNutritionEstimation,
    NutritionBackfillCompleted,
    NutritionBackfillEntryFailure,
    NutritionBackfillProgress,
    SkippedNutritionEstimation,
    StoredEntryNutritionEstimationUseCase,
    SuccessfulNutritionEstimation,
)
from food_registry_bot.nutrition.daily_summary import (
    DailyNutritionEntrySummary,
    DailyNutritionItemSummary,
    DailyNutritionSummary,
    DailyNutritionSummaryUseCase,
    DailyNutritionTotals,
    resolve_day_bounds_utc,
    resolve_local_summary_date,
)
from food_registry_bot.nutrition.water_summary import DailyWaterSummary, DailyWaterSummaryUseCase
from food_registry_bot.nutrition.goals import DailyNutritionGoalSnapshotUseCase
from food_registry_bot.nutrition.progress import (
    DailyNutritionGoalProgress,
    DailyNutritionGoalProgressUseCase,
    MetricGoalProgress,
)
from food_registry_bot.nutrition.workout_credit import (
    calculate_default_workout_calorie_credit,
    DailyWorkoutCalorieCreditUseCase,
    resolve_workout_metric_value,
)

__all__ = [
    "InvalidNutritionPayload",
    "LLMNutritionEstimationService",
    "LLMNutritionClient",
    "LLMNutritionClientError",
    "NutritionConfidence",
    "NutritionEstimationItemInput",
    "NutritionEstimationItemResult",
    "NutritionEstimationPayload",
    "NutritionEstimationRequest",
    "NutritionJournalItemRef",
    "NutritionMetricResult",
    "NutritionEstimationService",
    "NutritionPayloadClient",
    "SUPPORTED_NUTRITION_METRIC_CODES",
    "NutritionUnit",
    "OpenAIResponsesNutritionClient",
    "PreparedNutritionRequest",
    "ResolvedNutritionEstimate",
    "FailedNutritionEstimation",
    "SkippedNutritionEstimation",
    "StaticNutritionEstimationService",
    "StoredEntryNutritionEstimationUseCase",
    "SuccessfulNutritionEstimation",
    "ValidNutritionPayload",
    "create_nutrition_service",
    "BackfillNutritionEstimationUseCase",
    "DailyNutritionEntrySummary",
    "DailyNutritionItemSummary",
    "DailyNutritionSummary",
    "DailyNutritionSummaryUseCase",
    "DailyNutritionTotals",
    "DailyWaterSummary",
    "DailyWaterSummaryUseCase",
    "prepare_nutrition_request_from_entries",
    "prepare_nutrition_request_from_extracted_payload",
    "resolve_nutrition_estimates",
    "resolve_day_bounds_utc",
    "resolve_local_summary_date",
    "NutritionBackfillCompleted",
    "NutritionBackfillEntryFailure",
    "NutritionBackfillProgress",
    "DailyNutritionGoalSnapshotUseCase",
    "DailyNutritionGoalProgress",
    "DailyNutritionGoalProgressUseCase",
    "MetricGoalProgress",
    "calculate_default_workout_calorie_credit",
    "DailyWorkoutCalorieCreditUseCase",
    "resolve_workout_metric_value",
]
