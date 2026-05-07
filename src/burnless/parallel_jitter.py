"""Parallel-launch jitter (QTP-C).

Avoids API overload (529) cascades when multiple `burnless do` invocations
fire concurrently from shell backgrounding (`do … & do … & do … &`).

Mechanism: lockfile-based in-flight registry under temp/in_flight/. Before
launching a worker, count active locks; if any exist, sleep a small random
delay (0.5-2.5s) so concurrent launches space out instead of all hitting
the API at once.

Stale locks (>2h) are auto-pruned on access.
"""
from __future__ import annotations

import os
import random
import time
from contextlib import contextmanager
from pathlib import Path

_STALE_LOCK_S = 7200  # 2h


def _in_flight_dir(burnless_root: Path) -> Path:
    d = burnless_root / "temp" / "in_flight"
    d.mkdir(parents=True, exist_ok=True)
    return d


def count_in_flight(burnless_root: Path) -> int:
    """Count active workers; expire stale (>2h) locks as a side effect."""
    d = _in_flight_dir(burnless_root)
    now = time.time()
    count = 0
    for lock in d.glob("*.lock"):
        try:
            mt = lock.stat().st_mtime
        except FileNotFoundError:
            continue
        if now - mt > _STALE_LOCK_S:
            try:
                lock.unlink()
            except (OSError, FileNotFoundError):
                pass
            continue
        count += 1
    return count


def maybe_jitter(
    burnless_root: Path,
    *,
    min_s: float = 0.5,
    max_s: float = 2.5,
    enabled: bool = True,
) -> float:
    """Sleep random[min_s, max_s] s if other workers are in flight.

    Returns the sleep duration in seconds (0.0 if no jitter applied).
    """
    if not enabled:
        return 0.0
    if count_in_flight(burnless_root) <= 0:
        return 0.0
    delay = random.uniform(min_s, max_s)
    time.sleep(delay)
    return delay


@contextmanager
def in_flight(burnless_root: Path, did: str):
    """Context manager registering this worker; removes lock on exit."""
    d = _in_flight_dir(burnless_root)
    pid = os.getpid()
    lock_path = d / f"{did}_{pid}.lock"
    try:
        lock_path.touch()
        yield lock_path
    finally:
        try:
            lock_path.unlink()
        except (OSError, FileNotFoundError):
            pass
