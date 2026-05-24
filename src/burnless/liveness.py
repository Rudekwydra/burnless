"""Liveness probes for worker subprocess trees.

Lazy import psutil — fail gracefully if not installed.
"""
from __future__ import annotations
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore
    _PSUTIL_OK = True
except ImportError:
    psutil = None
    _PSUTIL_OK = False


def is_available() -> bool:
    return _PSUTIL_OK


def capture_io_baseline(worker_pid: int) -> dict[int, dict[str, int]]:
    """Capture {pid: {read_bytes, write_bytes}} for worker_pid + all descendants.
    Returns empty dict if psutil unavailable or worker_pid gone.
    """
    if not _PSUTIL_OK:
        return {}
    try:
        proc = psutil.Process(worker_pid)
        descendants = [proc] + proc.children(recursive=True)
        baseline = {}
        for p in descendants:
            try:
                io = p.io_counters()
                baseline[p.pid] = {
                    "read_bytes": io.read_bytes,
                    "write_bytes": io.write_bytes,
                }
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                continue
        return baseline
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {}


def io_changed_since(worker_pid: int, baseline: dict[int, dict[str, int]]) -> tuple[bool, dict[int, dict[str, int]]]:
    """Return (changed, new_baseline). changed=True if any descendant subprocess
    increased read_bytes or write_bytes since baseline. new_baseline updates the
    snapshot for next comparison.
    """
    new_baseline = capture_io_baseline(worker_pid)
    if not baseline or not new_baseline:
        return (False, new_baseline)
    for pid, current in new_baseline.items():
        prev = baseline.get(pid)
        if prev is None:
            # New subprocess appeared — count as activity
            return (True, new_baseline)
        if (current["read_bytes"] > prev["read_bytes"]
                or current["write_bytes"] > prev["write_bytes"]):
            return (True, new_baseline)
    return (False, new_baseline)


# ---------------------------------------------------------------------------
# JSONL event log — push-based liveness stream per delegation run
# ---------------------------------------------------------------------------

_start_times: dict[str, float] = {}


def init_run_dir(burnless_root: Path, did: str) -> Path:
    """Create .burnless/runs/<did>/ directory. Returns the path.
    Side effect: creates a fresh liveness.jsonl (truncated if exists from prior run)."""
    run_dir = burnless_root / "runs" / did
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl = run_dir / "liveness.jsonl"
    jsonl.write_text("", encoding="utf-8")
    return run_dir


def liveness_path(burnless_root: Path, did: str) -> Path:
    """Return Path to .burnless/runs/<did>/liveness.jsonl."""
    return burnless_root / "runs" / did / "liveness.jsonl"


def emit(burnless_root: Path, did: str, event: str, **payload) -> None:
    """Append a JSONL line to .burnless/runs/<did>/liveness.jsonl.
    Line format: {"ts": "<ISO8601 UTC>", "event": "<event>", "did": "<did>",
                  "elapsed_s": <float since liveness start>, "payload": {...}}.
    elapsed_s computed from a per-did start timestamp stored in module state
    (dict mapping did → start_monotonic_ts). Set on first emit(event="start").
    For events before "start", elapsed_s = 0.0.
    Best-effort: on IO error, swallow exception (do NOT crash worker)."""
    try:
        now_mono = time.monotonic()
        if event == "start":
            _start_times[did] = now_mono
        start_mono = _start_times.get(did)
        elapsed_s = (now_mono - start_mono) if start_mono is not None else 0.0
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "did": did,
            "elapsed_s": round(elapsed_s, 3),
            "payload": payload,
        }
        p = liveness_path(burnless_root, did)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def tail_events(burnless_root: Path, did: str, *, since_n: int = 0, follow: bool = True):
    """Generator yielding dict events from liveness.jsonl.
    since_n > 0: skip first N existing events, yield from N+1 onwards.
    follow=True: after consuming existing events, keep checking for new lines (poll 0.5s).
    follow=False: yield existing events then return.
    Yields parsed event dicts. Malformed lines are skipped silently."""
    p = liveness_path(burnless_root, did)
    if not follow and not p.exists():
        raise FileNotFoundError(p)
    skipped = 0
    with p.open("r", encoding="utf-8") as f:
        while True:
            line = f.readline()
            if line:
                if skipped < since_n:
                    skipped += 1
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    pass
            else:
                if not follow:
                    return
                time.sleep(0.5)
