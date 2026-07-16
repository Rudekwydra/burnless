from __future__ import annotations
from pathlib import Path

ROOT_DIR = ".burnless"


def root(cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    return base / ROOT_DIR


def is_project_root(d: Path) -> bool:
    """A real burnless project root: contains .burnless/config.yaml,
    is not $HOME itself, and is not inside the global state dir ~/.burnless."""
    home = Path.home()
    if d == home:
        return False
    state_dir = home / ".burnless"
    if d == state_dir or state_dir in d.parents:
        return False
    bl = d / ".burnless"
    return bl.is_dir() and (bl / "config.yaml").is_file()


def find_root(start: Path | None = None) -> Path | None:
    """Walk start..up; return the first VALIDATED project's .burnless dir.
    Stops at $HOME (exclusive). Never returns ~/.burnless or anything under it."""
    home = Path.home()
    cur = (start or Path.cwd()).resolve()
    while True:
        if is_project_root(cur):
            return cur / ROOT_DIR
        if cur == home or cur.parent == cur:
            return None
        cur = cur.parent


def require_root() -> Path:
    cwd = Path.cwd()
    r = find_root(cwd)
    if r is None:
        raise SystemExit(
            f"burnless: not initialized in {cwd} (no .burnless/config.yaml up-tree). "
            "run `burnless init`."
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
