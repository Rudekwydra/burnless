from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def create(
    burnless_root: Path,
    task_id: int,
    *,
    parent_capsule: str,
    tier: str,
    model: str,
) -> Path:
    started = datetime.now(timezone.utc)
    path = burnless_root / "exec_log" / f"T{task_id:04d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "id": f"T{task_id}",
        "parent_capsule": parent_capsule,
        "tier": tier,
        "model": model,
        "started": started.isoformat(),
        "ended": None,
        "duration_s": None,
        "status": "WIP",
        "files_touched": [],
        "validations": [],
        "issues": [],
        "tokens": {},
    }
    _write(path, frontmatter, "## Full transcript\n\n")
    return path


def finalize(
    path: Path,
    *,
    status: str,
    files_touched: list[str],
    validations: list[dict],
    issues: list[str],
    transcript: str,
    ended: datetime,
) -> None:
    frontmatter, _body = _read(path)
    started = _parse_datetime(frontmatter.get("started"))
    frontmatter.update(
        {
            "ended": ended.isoformat(),
            "duration_s": round((ended - started).total_seconds(), 3) if started else None,
            "status": status,
            "files_touched": files_touched,
            "validations": validations,
            "issues": issues,
        }
    )
    _write(path, frontmatter, "## Full transcript\n\n" + transcript.rstrip() + "\n")


def _write(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    path.write_text(
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
        + "---\n\n"
        + body,
        encoding="utf-8",
    )


def _read(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.startswith("---\n"):
        return {}, text
    try:
        _start, yaml_text, body = text.split("---\n", 2)
    except ValueError:
        return {}, text
    return yaml.safe_load(yaml_text) or {}, body


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
