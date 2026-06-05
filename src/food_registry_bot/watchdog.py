from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import threading
import time

from food_registry_bot.runtime_state import write_restart_marker

logger = logging.getLogger(__name__)


def read_heartbeat_timestamp(path: Path) -> float | None:
    try:
        raw_value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None

    if not raw_value:
        return None

    return float(raw_value)


def is_heartbeat_stale(
    *,
    current_time: float,
    last_heartbeat_time: float | None,
    timeout_seconds: float,
) -> bool:
    if last_heartbeat_time is None:
        return True
    return current_time - last_heartbeat_time > timeout_seconds


class HeartbeatMonitor:
    def __init__(
        self,
        *,
        heartbeat_file: Path,
        heartbeat_interval_seconds: float,
        timeout_seconds: float,
        restart_marker_file: Path,
    ) -> None:
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if timeout_seconds <= heartbeat_interval_seconds:
            raise ValueError("timeout_seconds must be greater than heartbeat_interval_seconds")

        self._heartbeat_file = heartbeat_file
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._restart_marker_file = restart_marker_file
        self._last_heartbeat_time: float | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._watchdog_thread: threading.Thread | None = None

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def heartbeat_file(self) -> Path:
        return self._heartbeat_file

    def beat(self) -> None:
        heartbeat_time = time.monotonic()
        self._heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        self._heartbeat_file.write_text(f"{heartbeat_time:.6f}\n", encoding="utf-8")
        with self._lock:
            self._last_heartbeat_time = heartbeat_time

    async def run_heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            self.beat()
            await asyncio.sleep(self._heartbeat_interval_seconds)

    def start_watchdog(self) -> None:
        if self._watchdog_thread is not None:
            return

        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="bot-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=1.0)

    def _watchdog_loop(self) -> None:
        sleep_seconds = max(1.0, min(self._heartbeat_interval_seconds, self._timeout_seconds / 3))
        while not self._stop_event.wait(sleep_seconds):
            with self._lock:
                last_heartbeat_time = self._last_heartbeat_time

            if not is_heartbeat_stale(
                current_time=time.monotonic(),
                last_heartbeat_time=last_heartbeat_time,
                timeout_seconds=self._timeout_seconds,
            ):
                continue

            logger.critical(
                "Watchdog detected a stale heartbeat. last_heartbeat_time=%s timeout_seconds=%s",
                last_heartbeat_time,
                self._timeout_seconds,
            )
            write_restart_marker(
                self._restart_marker_file,
                reason="watchdog: stale heartbeat detected",
            )
            os._exit(1)
