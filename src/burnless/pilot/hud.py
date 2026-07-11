from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path


def hud_title(project_root: str | Path, home: Path | None = None) -> str:
    """Generate compact HUD string with savings + worker count. Fail-open."""
    if home is None:
        home = Path.home()

    project_root = Path(project_root)

    try:
        tokens_total = 0

        savings_path = home / ".burnless" / "state" / "savings.json"
        if savings_path.exists():
            with open(savings_path) as f:
                data = json.load(f)
                tokens_total += int(data.get("workers", {}).get("tokens_offloaded", 0))
                tokens_total += int(data.get("capsules", {}).get("reuse_tokens_avoided", 0))
                tokens_total += int(data.get("clear", {}).get("context_avoided_total", 0))

        workers_live = 0
        logs_dir = project_root / ".burnless" / "logs"
        now = time.time()
        if logs_dir.exists():
            for log_file in logs_dir.glob("d*.log"):
                try:
                    mtime = log_file.stat().st_mtime
                    if now - mtime < 60:
                        workers_live += 1
                except Exception:
                    pass

        parts = ["burnless"]
        if tokens_total > 0:
            tokens_fmt = _fmt_tokens(tokens_total)
            parts.append(f"⚡{tokens_fmt} poupado")
        if workers_live > 0:
            parts.append(f"{workers_live} worker" + ("s" if workers_live != 1 else ""))

        return " · ".join(parts)
    except Exception:
        return "burnless"


def _fmt_tokens(n: int) -> str:
    """Format token count compactly: 1000000 → '1.0M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def osc_title(text: str) -> bytes:
    """Convert text to OSC 0 title sequence, sanitizing control chars."""
    text = re.sub(r'[\x1b\x07\x00-\x1f]', '', text)
    return b"\x1b]0;" + text.encode("utf-8", "replace") + b"\x07"
