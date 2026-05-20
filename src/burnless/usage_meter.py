"""Usage meter (native) for Burnless.

Goal: read local session logs (e.g. Claude Code JSONL) and compute a small,
audit-friendly usage delta for a time window.

This intentionally does NOT depend on third-party tools (e.g. codeburn).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class UsageDelta:
    window_seconds: int
    files_scanned: int
    lines_scanned: int
    calls: int
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int

    @property
    def cache_hit_rate(self) -> float:
        denom = self.cache_read_input_tokens + self.cache_creation_input_tokens
        if denom <= 0:
            return 0.0
        return self.cache_read_input_tokens / denom

    @property
    def cold_baseline_input_tokens(self) -> int:
        """Approx input tokens if the same window were run with a cold cache."""
        # Anthropic reports `input_tokens` excluding cache reads, and separately
        # reports `cache_read_input_tokens`. Treat cold baseline as the sum.
        return int(self.input_tokens + self.cache_read_input_tokens)

    @property
    def cache_spared_input_tokens(self) -> int:
        """How many input tokens were served from cache in this window."""
        return int(self.cache_read_input_tokens)


def _project_slug_from_path(path: Path) -> str:
    # Claude Code uses a directory name that looks like:
    #   -Users-roberto-antigravity-burnless
    # derived from the absolute path with "/" replaced by "-".
    p = path.expanduser().resolve()
    s = str(p)
    if s.startswith("/"):
        s = s[1:]
    s = s.replace("/", "-")
    return "-" + s


def claude_project_dir(*, cwd: Path | None = None) -> Path:
    base = Path.home() / ".claude" / "projects"
    return base / _project_slug_from_path(cwd or Path.cwd())


def _iter_recent_jsonl_files(dir_path: Path) -> list[Path]:
    if not dir_path.exists() or not dir_path.is_dir():
        return []
    files: list[Path] = []
    try:
        for p in dir_path.glob("*.jsonl"):
            try:
                _ = p.stat().st_mtime
                files.append(p)
            except OSError:
                continue
    except OSError:
        return []
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return files


def _parse_ts_maybe(obj: dict[str, Any]) -> float | None:
    ts = obj.get("timestamp")
    if not isinstance(ts, str) or not ts.strip():
        return None
    raw = ts.strip()
    try:
        # Common: 2026-05-18T22:57:27.064Z
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _extract_usage_from_record(obj: dict[str, Any]) -> dict[str, int] | None:
    # Claude Code JSONL: top-level record may have:
    #   {"type":"assistant", "message": {"usage": {...}}}
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None

    def _int(key: str) -> int:
        try:
            return int(usage.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "input_tokens": _int("input_tokens"),
        "output_tokens": _int("output_tokens"),
        "cache_read_input_tokens": _int("cache_read_input_tokens"),
        "cache_creation_input_tokens": _int("cache_creation_input_tokens"),
    }


def claude_usage_delta(
    *,
    cwd: Path | None = None,
    window_seconds: int = 15 * 60,
    max_files: int = 20,
    max_lines: int = 50_000,
) -> UsageDelta:
    """Aggregate Claude Code usage in a recent time window.

    Conservative: only counts records that contain explicit `message.usage`.
    """
    now = time.time()
    since_ts = now - max(10, int(window_seconds))
    project_dir = claude_project_dir(cwd=cwd)
    files = _iter_recent_jsonl_files(project_dir)[: max(1, int(max_files))]

    calls = input_tokens = output_tokens = cache_read = cache_create = 0
    lines_scanned = 0

    for f in files:
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if lines_scanned >= max_lines:
                        break
                    lines_scanned += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = _parse_ts_maybe(obj)
                    if ts is not None and ts < since_ts:
                        continue
                    u = _extract_usage_from_record(obj)  # may be None
                    if not u:
                        continue
                    calls += 1
                    input_tokens += u["input_tokens"]
                    output_tokens += u["output_tokens"]
                    cache_read += u["cache_read_input_tokens"]
                    cache_create += u["cache_creation_input_tokens"]
        except OSError:
            continue

    return UsageDelta(
        window_seconds=int(window_seconds),
        files_scanned=len(files),
        lines_scanned=int(lines_scanned),
        calls=int(calls),
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cache_read_input_tokens=int(cache_read),
        cache_creation_input_tokens=int(cache_create),
    )


def fmt_compact(delta: UsageDelta) -> str:
    """One-line compact label for TUI/PTY footers."""
    if delta.calls <= 0:
        return "cache --"
    hit = int(round(delta.cache_hit_rate * 100))
    # Keep it short: hit% + read tokens.
    rd = delta.cache_read_input_tokens
    if rd >= 1_000_000:
        rd_s = f"{rd/1_000_000:.1f}M"
    elif rd >= 1_000:
        rd_s = f"{rd/1_000:.1f}k"
    else:
        rd_s = str(rd)
    spared = delta.cache_spared_input_tokens
    if spared >= 1_000_000:
        spared_s = f"{spared/1_000_000:.1f}M"
    elif spared >= 1_000:
        spared_s = f"{spared/1_000:.1f}k"
    else:
        spared_s = str(spared)
    return f"cache {hit}% · spared {spared_s} · calls {delta.calls}"
