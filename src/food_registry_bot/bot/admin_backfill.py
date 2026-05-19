from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class AdminBackfillSnapshot:
    state: str
    limit: int | None
    selected_entry_count: int
    processed_entry_count: int
    skipped_entry_count: int
    failed_entry_count: int
    requested_by: int | None
    started_at: datetime | None
    finished_at: datetime | None
    last_message: str | None


class AdminBackfillTracker:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._state = "idle"
        self._limit: int | None = None
        self._selected_entry_count = 0
        self._processed_entry_count = 0
        self._skipped_entry_count = 0
        self._failed_entry_count = 0
        self._requested_by: int | None = None
        self._started_at: datetime | None = None
        self._finished_at: datetime | None = None
        self._last_message: str | None = None

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, *, requested_by: int, limit: int, task: asyncio.Task) -> None:
        self._task = task
        self._state = "running"
        self._limit = limit
        self._selected_entry_count = 0
        self._processed_entry_count = 0
        self._skipped_entry_count = 0
        self._failed_entry_count = 0
        self._requested_by = requested_by
        self._started_at = datetime.now(timezone.utc)
        self._finished_at = None
        self._last_message = None

    def update_progress(
        self,
        *,
        selected_entry_count: int,
        processed_entry_count: int,
        skipped_entry_count: int,
        failed_entry_count: int,
    ) -> None:
        self._selected_entry_count = selected_entry_count
        self._processed_entry_count = processed_entry_count
        self._skipped_entry_count = skipped_entry_count
        self._failed_entry_count = failed_entry_count

    def mark_completed(self, *, message: str) -> None:
        self._state = "completed"
        self._finished_at = datetime.now(timezone.utc)
        self._last_message = message

    def mark_failed(self, *, message: str) -> None:
        self._state = "failed"
        self._finished_at = datetime.now(timezone.utc)
        self._last_message = message

    def snapshot(self) -> AdminBackfillSnapshot:
        return AdminBackfillSnapshot(
            state=self._state,
            limit=self._limit,
            selected_entry_count=self._selected_entry_count,
            processed_entry_count=self._processed_entry_count,
            skipped_entry_count=self._skipped_entry_count,
            failed_entry_count=self._failed_entry_count,
            requested_by=self._requested_by,
            started_at=self._started_at,
            finished_at=self._finished_at,
            last_message=self._last_message,
        )
