from __future__ import annotations
from pathlib import Path

ROOT_DIR = ".burnless"


def root(cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    return base / ROOT_DIR


def find_root(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    while True:
        candidate = cur / ROOT_DIR
        if candidate.is_dir():
            return candidate
        if cur.parent == cur:
            return None
        cur = cur.parent


def require_root() -> Path:
    r = find_root()
    if r is None:
        raise SystemExit(
            "burnless: not initialized in this directory tree. run `burnless init` first."
        )
    return r


def paths_for(root_dir: Path) -> dict[str, Path]:
    return {
        "root": root_dir,
        "config": root_dir / "config.yaml",
        "state": root_dir / "state.json",
        "metrics": root_dir / "metrics.json",
        "audit": root_dir / "audit.jsonl",
        "maestro": root_dir / "maestro.md",
        "chat": root_dir / "chat",
        "history": root_dir / "chat" / "history.md",
        "delegations": root_dir / "delegations",
        "logs": root_dir / "logs",
        "temp": root_dir / "temp",
        "capsules": root_dir / "capsules",
        "runs": root_dir / "runs",
        "archive": root_dir / "archive",
    }
