from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.admin_backfill import AdminBackfillTracker
from food_registry_bot.bot.handlers import router
from food_registry_bot.bot.message_routing import RuleBasedMessageRoutingService
from food_registry_bot.conversation import ConversationService
from food_registry_bot.config import Settings
from food_registry_bot.extraction import JournalExtractionService
from food_registry_bot.nutrition import NutritionEstimationService


def create_bot(token: str) -> Bot:
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    session_factory: sessionmaker[Session],
    extraction_service: JournalExtractionService,
    nutrition_service: NutritionEstimationService,
    conversation_service: ConversationService,
    admin_user_ids: tuple[int, ...],
    settings: Settings | None = None,
    app_version: str = "unknown",
) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher.workflow_data["session_factory"] = session_factory
    dispatcher.workflow_data["extraction_service"] = extraction_service
    dispatcher.workflow_data["nutrition_service"] = nutrition_service
    dispatcher.workflow_data["conversation_service"] = conversation_service
    dispatcher.workflow_data["message_routing_service"] = RuleBasedMessageRoutingService()
    dispatcher.workflow_data["admin_user_ids"] = admin_user_ids
    dispatcher.workflow_data["backfill_tracker"] = AdminBackfillTracker()
    dispatcher.workflow_data["settings"] = settings
    dispatcher.workflow_data["app_version"] = app_version
    return dispatcher
