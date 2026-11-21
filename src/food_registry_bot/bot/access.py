from __future__ import annotations

from sqlalchemy.orm import Session

from food_registry_bot.db.repositories import UserAccessRepository


def can_access_bot(
    session: Session, telegram_user_id: int, admin_user_ids: tuple[int, ...]
) -> bool:
    return telegram_user_id in admin_user_ids or UserAccessRepository(session).is_allowed(
        telegram_user_id
    )
