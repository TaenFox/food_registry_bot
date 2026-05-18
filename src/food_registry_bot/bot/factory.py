from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.bot.handlers import router
from food_registry_bot.extraction import JournalExtractionService


def create_bot(token: str) -> Bot:
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    session_factory: sessionmaker[Session],
    extraction_service: JournalExtractionService,
) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher.workflow_data["session_factory"] = session_factory
    dispatcher.workflow_data["extraction_service"] = extraction_service
    return dispatcher
