from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from food_registry_bot.db.models import AccountCategory, User
from food_registry_bot.db.repositories import (
    DEFAULT_SUMMARY_PREFERENCES,
    UserAccessRepository,
    UserSummaryPreferenceRepository,
)
from food_registry_bot.llm_access.resolver import ResolvedProviderAccess
from food_registry_bot.nutrition.daily_summary import resolve_day_bounds_utc, resolve_local_summary_date

TrialAction = Literal["food", "conversation"]
TRIAL_DAILY_LIMITS: dict[TrialAction, int] = {"food": 7, "conversation": 20}


@dataclass
class TrialDay:
    resets_at: datetime
    used: dict[TrialAction, int] = field(default_factory=dict)


class TrialQuota:
    def __init__(self) -> None:
        self._lock = Lock()
        self._days: dict[int, TrialDay] = {}
        self._prune_at = datetime.min.replace(tzinfo=timezone.utc)

    def _active_day(self, user_id: int, now: datetime) -> TrialDay | None:
        day = self._days.get(user_id)
        return day if day is not None and day.resets_at > now else None

    def remaining(self, user_id: int, now: datetime) -> dict[TrialAction, int]:
        with self._lock:
            day = self._active_day(user_id, now)
            return {action: limit - (day.used.get(action, 0) if day else 0)
                    for action, limit in TRIAL_DAILY_LIMITS.items()}

    def consume(
        self, user_id: int, action: TrialAction, *,
        now: datetime, timezone_name: str, start_hour: int,
    ) -> datetime | None:
        with self._lock:
            if now >= self._prune_at:
                self._days = {key: day for key, day in self._days.items() if day.resets_at > now}
                self._prune_at = now + timedelta(hours=1)
            day = self._active_day(user_id, now)
            if day is None:
                summary_date = resolve_local_summary_date(
                    reference_at=now, timezone_name=timezone_name, nutrition_day_start_hour=start_hour,
                )
                _, resets_at = resolve_day_bounds_utc(
                    summary_date=summary_date, timezone_name=timezone_name, nutrition_day_start_hour=start_hour,
                )
                if resets_at <= now:
                    _, resets_at = resolve_day_bounds_utc(
                        summary_date=summary_date + timedelta(days=1), timezone_name=timezone_name,
                        nutrition_day_start_hour=start_hour,
                    )
                day = self._days[user_id] = TrialDay(resets_at)
            used = day.used.get(action, 0)
            if used >= TRIAL_DAILY_LIMITS[action]:
                return day.resets_at
            day.used[action] = used + 1
            return None


def charge_trial_request(
    quota: TrialQuota | None, session: Session, user: User, admin_user_ids: tuple[int, ...],
    action: TrialAction, provider: ResolvedProviderAccess | None,
) -> datetime | None:
    if quota is None or user.telegram_user_id in admin_user_ids:
        return None
    if provider is None or not provider.is_available or provider.source != "project":
        return None
    repository = UserAccessRepository(session)
    access = repository.get_by_telegram_user_id(user.telegram_user_id)
    if access is None or access.account_category is AccountCategory.INTERNAL:
        return None
    if access.temporary_internal_until is None:
        return None
    now = datetime.now(timezone.utc)
    preference = UserSummaryPreferenceRepository(session).get_by_user_id(user.id)
    return quota.consume(
        user.id, action, now=now, timezone_name=user.timezone,
        start_hour=(preference.nutrition_day_start_hour if preference else
                    DEFAULT_SUMMARY_PREFERENCES["nutrition_day_start_hour"]),
    )


def trial_limit_message(action: TrialAction, resets_at: datetime, timezone_name: str) -> str:
    feature = "ИИ-разбора еды" if action == "food" else "диалога с ИИ"
    local_reset = resets_at.astimezone(ZoneInfo(timezone_name))
    return f"Дневной лимит {feature} исчерпан. Следующий сброс: {local_reset:%d.%m в %H:%M}."


def trial_has_expired(session: Session, telegram_user_id: int) -> bool:
    repository = UserAccessRepository(session)
    access = repository.get_by_telegram_user_id(telegram_user_id)
    return bool(
        access and access.account_category is not AccountCategory.INTERNAL
        and access.temporary_internal_until is not None
        and not repository.is_temporary_internal_active(access)
    )
