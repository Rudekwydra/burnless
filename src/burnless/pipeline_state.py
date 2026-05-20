"""3-layer pipeline state — toggle on/off + statusline data.

State file: ~/.burnless/state/pipeline-<project-hash>.active
Presence = pipeline ON. Absence = pipeline OFF.
"""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path


STATE_DIR = Path.home() / ".burnless" / "state"


def _project_key(project_root: Path) -> str:
    return hashlib.sha1(str(project_root.resolve()).encode("utf-8")).hexdigest()[:12]


def _state_file(project_root: Path) -> Path:
    return STATE_DIR / f"pipeline-{_project_key(project_root)}.active"


def is_active(project_root: Path) -> bool:
    return _state_file(project_root).exists()


def activate(project_root: Path, compression_mode: str = "tight") -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "activated_at": time.time(),
        "compression_mode": compression_mode,
        "project_root": str(project_root.resolve()),
        "turn_count": 0,
    }
    _state_file(project_root).write_text(json.dumps(payload, indent=2))
    return payload


def deactivate(project_root: Path) -> bool:
    f = _state_file(project_root)
    if f.exists():
        f.unlink()
        return True
    return False


def read_state(project_root: Path) -> dict | None:
    f = _state_file(project_root)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def increment_turn(project_root: Path) -> int:
    state = read_state(project_root)
    if not state:
        return 0
    state["turn_count"] = int(state.get("turn_count", 0)) + 1
    _state_file(project_root).write_text(json.dumps(state, indent=2))
    return state["turn_count"]


def statusline(project_root: Path) -> str:
    state = read_state(project_root)
    if not state:
        return ""
    turns = state.get("turn_count", 0)
    mode = state.get("compression_mode", "tight")
    hint = "  consider /clear" if turns > 0 and turns % 25 == 0 else ""
    return f"burnless pipeline ON · mode={mode} · turns={turns}{hint}"
