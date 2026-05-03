from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone


def lifetime_path() -> Path:
    override = os.environ.get("BURNLESS_LIFETIME_PATH")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "burnless" / "lifetime.json"
    return Path.home() / ".config" / "burnless" / "lifetime.json"


def load() -> dict:
    path = lifetime_path()
    if not path.exists():
        return _fresh()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        print(f"burnless: ignoring corrupted lifetime file at {path}", file=sys.stderr)
        return _fresh()
    except OSError as e:
        print(f"burnless: could not read lifetime file: {e}", file=sys.stderr)
        return _fresh()

    base = _fresh()
    for key, value in base.items():
        data.setdefault(key, value)
    if not isinstance(data.get("projects"), list):
        data["projects"] = []
    data["total_saved_usd"] = max(float(data.get("total_saved_usd") or 0.0), 0.0)
    data["capsules_total"] = max(int(data.get("capsules_total") or 0), 0)
    return data


def save(data: dict) -> None:
    path = lifetime_path()
    data["updated_at"] = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def bump(*, project_root: Path, usd_delta: float = 0.0, capsules_delta: int = 0) -> dict:
    data = load()
    now = _now_iso()
    if not data.get("first_use_at"):
        data["first_use_at"] = now

    project = str(project_root.resolve())
    projects = list(data.get("projects") or [])
    if project not in projects:
        projects.append(project)
    data["projects"] = projects

    if usd_delta > 0:
        data["total_saved_usd"] = round(float(data.get("total_saved_usd") or 0.0) + usd_delta, 4)
    if capsules_delta > 0:
        data["capsules_total"] = int(data.get("capsules_total") or 0) + capsules_delta

    try:
        save(data)
    except OSError as e:
        print(f"burnless: could not update lifetime file: {e}", file=sys.stderr)
    return data


def summary_line(data: dict, language: str) -> str:
    saved = float(data.get("total_saved_usd") or 0.0)
    projects = len(data.get("projects") or [])
    since = (data.get("first_use_at") or _now_iso())[:10]
    if language == "en-US":
        return f"lifetime saved: US${saved:,.2f} across {projects} projects since {since}"
    return f"vida toda: US${saved:,.2f} em {projects} projetos desde {since}"


def _fresh() -> dict:
    return {
        "first_use_at": None,
        "total_saved_usd": 0.0,
        "projects": [],
        "capsules_total": 0,
        "updated_at": None,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
