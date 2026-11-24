from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.config import Settings
from food_registry_bot.db.models import Entry, LLMIssueStage
from food_registry_bot.db.repositories import (
    EntryItemCreate,
    EntryItemMetricRepository,
    EntryItemMetricValue,
    EntryRepository,
    LLMIssueLogCreate,
    LLMIssueLogRepository,
)
from food_registry_bot.db.session import session_scope
from food_registry_bot.diet import (
    DietDefinitionInput,
    DietEvaluationItemInput,
    DietEvaluationItemResult,
    DietEvaluationPayload,
    DietEvaluationRequest,
    DietEvaluationService,
    DietScoreResult,
    create_diet_service_for_provider_access,
    get_supported_diet_definition,
)
from food_registry_bot.extraction.contract import ExtractedJournalEntry
from food_registry_bot.extraction.service import ValidExtractionPayload


def persist_extracted_entry(
    *,
    session: Session,
    user_id: int,
    extracted_entry: ExtractedJournalEntry,
    extraction_result: ValidExtractionPayload,
    occurred_at: datetime,
    source_text: str | None,
) -> Entry:
    return EntryRepository(session).create(
        user_id=user_id,
        entry_type=extracted_entry.type,
        occurred_at=occurred_at,
        source_text=source_text,
        extraction_provider=extraction_result.extraction_provider,
        extraction_model=extraction_result.extraction_model,
        extraction_raw_payload=json.dumps(
            {"entries": [extracted_entry.model_dump(mode="json")]}, ensure_ascii=False
        ),
        items=[
            EntryItemCreate(
                name=item.name, quantity=item.quantity, unit=item.unit,
                source_type="extraction_payload",
            )
            for item in extracted_entry.items
        ],
    )


def log_llm_issue(
    *,
    session_factory: sessionmaker[Session],
    stage: LLMIssueStage,
    error_code: str | None,
    provider: str | None,
    model: str | None,
    telegram_user_id: int | None,
    username: str | None,
    request_text: str | None,
    raw_payload: str | None,
    technical_message: str | None,
) -> None:
    with session_scope(session_factory) as session:
        LLMIssueLogRepository(session).create(
            LLMIssueLogCreate(
                stage=stage,
                error_code=error_code or "unknown_error",
                provider=provider,
                model=model,
                telegram_user_id=telegram_user_id,
                username=username,
                request_text=request_text,
                raw_payload=raw_payload,
                technical_message=technical_message,
            )
        )


def resolve_diet_service(
    *,
    settings: Settings | None,
    runtime_bundle,
    fallback_diet_service: DietEvaluationService,
) -> DietEvaluationService:
    if settings is None:
        return fallback_diet_service
    if runtime_bundle is None or runtime_bundle.conversation_access is None:
        return fallback_diet_service
    access = runtime_bundle.conversation_access
    if not access.is_available or access.api_key is None or access.model is None:
        return fallback_diet_service
    return create_diet_service_for_provider_access(
        provider=access.provider,
        api_key=access.api_key,
        model=access.model,
        settings=settings,
    )


def build_diet_evaluation_request_for_supported_diets(entries: list, diets: list) -> DietEvaluationRequest | None:
    if not entries or not diets:
        return None

    diet_inputs: list[DietDefinitionInput] = []
    for diet in diets:
        definition = get_supported_diet_definition(diet.code)
        diet_inputs.append(
            DietDefinitionInput(
                code=definition.code,
                name=definition.name,
                metric_code=definition.metric_code,
                description=definition.description,
                scoring_guidance=definition.scoring_guidance,
            )
        )

    items: list[DietEvaluationItemInput] = []
    for entry in entries:
        for item in sorted(entry.items, key=lambda current: current.position):
            if item.name == "water":
                continue
            items.append(
                DietEvaluationItemInput(
                    client_item_id=f"entry-{entry.id}:item-{item.position}",
                    name=item.name,
                    quantity=item.quantity,
                    unit=item.unit,
                )
            )

    if not items:
        return None

    return DietEvaluationRequest(diets=diet_inputs, items=items)


def build_fixed_diet_payload_for_water_entries(entries: list, diets: list) -> DietEvaluationPayload | None:
    if not entries or not diets:
        return None

    item_results: list[DietEvaluationItemResult] = []
    for entry in entries:
        for item in sorted(entry.items, key=lambda current: current.position):
            if item.name != "water":
                continue
            scores = []
            for diet in diets:
                definition = get_supported_diet_definition(diet.code)
                if definition.water_score is None:
                    continue
                scores.append(
                    DietScoreResult(
                        code=definition.metric_code,
                        value=definition.water_score,
                        confidence="high",
                    )
                )
            if not scores:
                continue
            item_results.append(
                DietEvaluationItemResult(
                    client_item_id=f"entry-{entry.id}:item-{item.position}",
                    scores=scores,
                )
            )

    if not item_results:
        return None
    return DietEvaluationPayload(items=item_results)


def persist_diet_scores(*, session: Session, entries: list, evaluation_payload) -> None:
    item_id_by_client_item_id = {
        f"entry-{entry.id}:item-{item.position}": item.id
        for entry in entries
        for item in entry.items
    }
    repository = EntryItemMetricRepository(session)
    for item_result in evaluation_payload.items:
        entry_item_id = item_id_by_client_item_id.get(item_result.client_item_id)
        if entry_item_id is None:
            continue
        repository.upsert_metrics(
            entry_item_id=entry_item_id,
            metric_values=[
                EntryItemMetricValue(
                    code=score.code,
                    value=score.value,
                    confidence=score.confidence,
                )
                for score in item_result.scores
            ],
        )
