from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Iterable

from .core import PilotEvent, HostAdapter


def _root_for(run_dir: Path) -> Path:
    return run_dir.parent.parent.parent if run_dir.name == "runs" else run_dir


def runs_dir(root: Path) -> Path:
    return root / ".burnless" / "pilot" / "runs"


def run_dir(root: Path, run_id: str) -> Path:
    return runs_dir(root) / run_id


def events_path(root: Path, run_id: str) -> Path:
    return run_dir(root, run_id) / "events.jsonl"


def session_log_path(root: Path) -> Path:
    return root / ".burnless" / "pilot" / "session_log.jsonl"


def append_event(root: Path, run_id: str, event: PilotEvent | dict) -> None:
    payload = asdict(event) if is_dataclass(event) else dict(event)
    path = events_path(root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def normalize_and_append_event(root: Path, run_id: str, adapter: HostAdapter, payload: dict) -> PilotEvent:
    event = adapter.normalize_hook_event(payload)
    append_event(root, run_id, event)
    return event


def read_events(root: Path, run_id: str, limit: int | None = None) -> list[dict]:
    path = events_path(root, run_id)
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit is not None:
        return rows[-limit:]
    return rows


def summarize_run_events(root: Path, run_id: str) -> dict:
    rows = read_events(root, run_id)
    if not rows:
        return {"count": 0, "last_event": None, "idle": False, "state": "unknown"}
    last = rows[-1]
    last_event = str(last.get("event") or "")
    active_events = {"turn_start", "prompt_submitted", "input_pending", "assistant_stream", "assistant_delta"}
    idle_events = {"turn", "turn_end", "session_reset", "stop", "clear", "session_start"}
    if last_event in active_events:
        idle = False
        state = "active"
    elif last_event in idle_events:
        idle = True
        state = "idle"
    else:
        idle = False
        state = "unknown"
    return {
        "count": len(rows),
        "last_event": last_event,
        "idle": idle,
        "state": state,
        "last": last,
    }


def append_session_log(root: Path, row: dict) -> None:
    path = session_log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_session_log(root: Path, limit: int | None = None) -> list[dict]:
    path = session_log_path(root)
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit is not None:
        return rows[-limit:]
    return rows


def summarize_session_log(root: Path) -> dict:
    rows = read_session_log(root)
    if not rows:
        return {"count": 0, "last": None}
    last = rows[-1]
    strategy = last.get("strategy") or last.get("rollover_mode") or "respawn"
    return {
        "count": len(rows),
        "last": last,
        "strategy": strategy,
        "host": last.get("host"),
        "host_version": last.get("host_version"),
        "host_session_id": last.get("old_session") or last.get("host_session_id"),
        "new_session_id": last.get("new_session"),
        "context_confidence": last.get("context_confidence") or last.get("usage_confidence") or "unknown",
        "context_before": last.get("context_before"),
        "checkpoint_chars": last.get("checkpoint_chars"),
        "pending_count": last.get("pending_count"),
        "turns": last.get("turns"),
        "journal_head": last.get("journal_head"),
        "applied_through": last.get("applied_through"),
    }
