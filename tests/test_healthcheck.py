from __future__ import annotations

import time
from pathlib import Path

from food_registry_bot.healthcheck import check_heartbeat_file
from food_registry_bot.runtime_state import (
    clear_restart_marker,
    read_restart_marker,
    runtime_state_exists,
    write_restart_marker,
    write_runtime_state,
)
from food_registry_bot.watchdog import HeartbeatMonitor, is_heartbeat_stale, read_heartbeat_timestamp


def test_is_heartbeat_stale_returns_false_for_recent_heartbeat() -> None:
    assert is_heartbeat_stale(
        current_time=100.0,
        last_heartbeat_time=40.5,
        timeout_seconds=60.0,
    ) is False


def test_is_heartbeat_stale_returns_true_for_missing_heartbeat() -> None:
    assert is_heartbeat_stale(
        current_time=100.0,
        last_heartbeat_time=None,
        timeout_seconds=60.0,
    ) is True


def test_heartbeat_monitor_writes_heartbeat_file(tmp_path: Path) -> None:
    heartbeat_file = tmp_path / "run" / "bot-heartbeat"
    monitor = HeartbeatMonitor(
        heartbeat_file=heartbeat_file,
        heartbeat_interval_seconds=1.0,
        timeout_seconds=5.0,
        restart_marker_file=tmp_path / "run" / "restart-marker.json",
    )

    monitor.beat()

    heartbeat_value = read_heartbeat_timestamp(heartbeat_file)
    assert heartbeat_value is not None
    assert heartbeat_value <= time.monotonic()


def test_check_heartbeat_file_reports_healthy_for_recent_heartbeat(tmp_path: Path) -> None:
    heartbeat_file = tmp_path / "bot-heartbeat"
    heartbeat_file.write_text(f"{time.monotonic():.6f}\n", encoding="utf-8")

    is_healthy, message = check_heartbeat_file(
        heartbeat_file=heartbeat_file,
        timeout_seconds=30.0,
    )

    assert is_healthy is True
    assert message == "ok"


def test_check_heartbeat_file_reports_unhealthy_for_stale_heartbeat(tmp_path: Path) -> None:
    heartbeat_file = tmp_path / "bot-heartbeat"
    heartbeat_file.write_text("1.0\n", encoding="utf-8")

    is_healthy, message = check_heartbeat_file(
        heartbeat_file=heartbeat_file,
        timeout_seconds=0.1,
    )

    assert is_healthy is False
    assert str(heartbeat_file) in message


def test_runtime_state_exists_after_write(tmp_path: Path) -> None:
    runtime_state_file = tmp_path / "bot-runtime-state.json"

    write_runtime_state(runtime_state_file)

    assert runtime_state_exists(runtime_state_file) is True


def test_restart_marker_roundtrip(tmp_path: Path) -> None:
    restart_marker_file = tmp_path / "bot-restart-marker.json"

    write_restart_marker(restart_marker_file, reason="watchdog: stale heartbeat detected")
    marker = read_restart_marker(restart_marker_file)

    assert marker is not None
    assert marker.reason == "watchdog: stale heartbeat detected"
    assert marker.recorded_at

    clear_restart_marker(restart_marker_file)
    assert read_restart_marker(restart_marker_file) is None
