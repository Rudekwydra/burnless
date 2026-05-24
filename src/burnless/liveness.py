"""Liveness probes for worker subprocess trees.

Lazy import psutil — fail gracefully if not installed.
"""
from __future__ import annotations
import time
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
