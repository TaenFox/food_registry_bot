from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class RestartMarker:
    reason: str
    recorded_at: str


def write_runtime_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "started_at": utc_now_iso(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def clear_runtime_state(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def runtime_state_exists(path: Path) -> bool:
    return path.exists()


def write_restart_marker(path: Path, *, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "reason": reason,
        "recorded_at": utc_now_iso(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def read_restart_marker(path: Path) -> RestartMarker | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None

    reason = payload.get("reason")
    recorded_at = payload.get("recorded_at")
    if not isinstance(reason, str) or not isinstance(recorded_at, str):
        return None

    return RestartMarker(reason=reason, recorded_at=recorded_at)


def clear_restart_marker(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
