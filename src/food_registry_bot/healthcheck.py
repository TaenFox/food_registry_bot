from __future__ import annotations

from pathlib import Path
import sys
import time

from food_registry_bot.config import get_settings
from food_registry_bot.watchdog import is_heartbeat_stale, read_heartbeat_timestamp


def check_heartbeat_file(*, heartbeat_file: Path, timeout_seconds: float) -> tuple[bool, str]:
    last_heartbeat_time = read_heartbeat_timestamp(heartbeat_file)
    if is_heartbeat_stale(
        current_time=time.monotonic(),
        last_heartbeat_time=last_heartbeat_time,
        timeout_seconds=timeout_seconds,
    ):
        return False, f"stale heartbeat in {heartbeat_file}"

    return True, "ok"


def main() -> int:
    settings = get_settings()
    if not settings.watchdog_enabled:
        print("watchdog disabled")
        return 0

    is_healthy, message = check_heartbeat_file(
        heartbeat_file=settings.heartbeat_file,
        timeout_seconds=settings.watchdog_timeout_seconds,
    )
    print(message)
    return 0 if is_healthy else 1


if __name__ == "__main__":
    sys.exit(main())
