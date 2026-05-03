from __future__ import annotations

from datetime import datetime
from pathlib import Path


HEADER = "# Burnless Chat History\n"


def ensure(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(HEADER, encoding="utf-8")


def append(path: Path, *, user: str, burnless: str) -> None:
    ensure(path)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = (
        f"\n## {ts}\n\n"
        f"User:\n{user.strip()}\n\n"
        f"Burnless:\n{burnless.strip()}\n"
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)
