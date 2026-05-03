from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

DEFAULT_STATE: dict = {
    "project": "Project",
    "last_delegation": None,
    "last_status": None,
    "next": None,
    "delegation_counter": 0,
    "active_tier": None,  # None = auto routing; "diamond"|"gold"|"silver"|"bronze" = sticky
    "updated_at": None,
}


def load(path: Path) -> dict:
    if not path.exists():
        return dict(DEFAULT_STATE)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    for k, v in DEFAULT_STATE.items():
        data.setdefault(k, v)
    return data


def save(path: Path, state: dict) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def next_delegation_id(state: dict) -> str:
    state["delegation_counter"] = int(state.get("delegation_counter", 0)) + 1
    return f"d{state['delegation_counter']:03d}"
