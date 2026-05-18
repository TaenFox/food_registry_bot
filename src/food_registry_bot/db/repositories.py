from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from food_registry_bot.db.models import Entry, EntryItem, EntryType, MealType, User


@dataclass(frozen=True)
class EntryItemCreate:
    name: str
    quantity: int | None = None
    unit: str | None = None
    confidence: str | None = None
    source_type: str | None = None


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_telegram_user_id(self, telegram_user_id: int) -> Optional[User]:
        statement = select(User).where(User.telegram_user_id == telegram_user_id)
        return self._session.scalar(statement)

    def create(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        timezone: str = "Europe/Moscow",
    ) -> User:
        user = User(
            telegram_user_id=telegram_user_id,
            username=username,
            timezone=timezone,
        )
        self._session.add(user)
        self._session.flush()
        return user

    def get_or_create(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        timezone: str = "Europe/Moscow",
    ) -> tuple[User, bool]:
        user = self.get_by_telegram_user_id(telegram_user_id)
        if user is not None:
            return user, False

        user = self.create(
            telegram_user_id=telegram_user_id,
            username=username,
            timezone=timezone,
        )
        return user, True


class EntryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        user_id: int,
        entry_type: EntryType,
        occurred_at: datetime,
        meal_type: MealType | None = None,
        source_text: str | None = None,
        extraction_provider: str | None = None,
        extraction_model: str | None = None,
        extraction_raw_payload: str | None = None,
        llm_comment: str | None = None,
        items: list[EntryItemCreate] | None = None,
    ) -> Entry:
        entry = Entry(
            user_id=user_id,
            entry_type=entry_type,
            meal_type=meal_type,
            source_text=source_text,
            extraction_provider=extraction_provider,
            extraction_model=extraction_model,
            extraction_raw_payload=extraction_raw_payload,
            llm_comment=llm_comment,
            occurred_at=occurred_at,
        )
        self._session.add(entry)
        self._session.flush()

        for position, item in enumerate(items or []):
            self._session.add(
                EntryItem(
                    entry_id=entry.id,
                    position=position,
                    name=item.name,
                    quantity=item.quantity,
                    unit=item.unit,
                    confidence=item.confidence,
                    source_type=item.source_type,
                )
            )

        self._session.flush()
        return entry

    def list_recent_for_user(self, *, user_id: int, limit: int = 5) -> list[Entry]:
        statement = (
            select(Entry)
            .where(Entry.user_id == user_id)
            .options(selectinload(Entry.items))
            .order_by(Entry.occurred_at.desc(), Entry.id.desc())
            .limit(limit)
        )
        return list(self._session.scalars(statement))
