"""QTP-C: parallel jitter + in-flight registry tests."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from burnless import parallel_jitter as pj


def test_count_in_flight_zero_when_empty(tmp_path: Path):
    assert pj.count_in_flight(tmp_path) == 0


def test_in_flight_context_manager_creates_and_removes_lock(tmp_path: Path):
    assert pj.count_in_flight(tmp_path) == 0
    with pj.in_flight(tmp_path, "d001"):
        assert pj.count_in_flight(tmp_path) == 1
    assert pj.count_in_flight(tmp_path) == 0


def test_in_flight_concurrent_workers_counted(tmp_path: Path):
    with pj.in_flight(tmp_path, "d001"):
        with pj.in_flight(tmp_path, "d002"):
            assert pj.count_in_flight(tmp_path) == 2
        assert pj.count_in_flight(tmp_path) == 1
    assert pj.count_in_flight(tmp_path) == 0


def test_stale_locks_pruned(tmp_path: Path):
    d = pj._in_flight_dir(tmp_path)
    stale = d / "d999_999.lock"
    stale.touch()
    # Set mtime to 3h ago
    old = time.time() - 10800
    os.utime(stale, (old, old))
    assert pj.count_in_flight(tmp_path) == 0  # stale pruned
    assert not stale.exists()


def test_maybe_jitter_skips_when_disabled(tmp_path: Path):
    with pj.in_flight(tmp_path, "d001"):
        delay = pj.maybe_jitter(tmp_path, enabled=False)
    assert delay == 0.0


def test_maybe_jitter_skips_when_alone(tmp_path: Path):
    delay = pj.maybe_jitter(tmp_path, min_s=0.01, max_s=0.02, enabled=True)
    assert delay == 0.0


def test_maybe_jitter_sleeps_when_others_in_flight(tmp_path: Path):
    with pj.in_flight(tmp_path, "d001"):
        start = time.monotonic()
        delay = pj.maybe_jitter(tmp_path, min_s=0.05, max_s=0.06, enabled=True)
        elapsed = time.monotonic() - start
    assert 0.04 <= delay <= 0.07
    assert elapsed >= 0.04


def test_in_flight_releases_lock_on_exception(tmp_path: Path):
    with pytest.raises(RuntimeError):
        with pj.in_flight(tmp_path, "d001"):
            assert pj.count_in_flight(tmp_path) == 1
            raise RuntimeError("simulated worker crash")
    assert pj.count_in_flight(tmp_path) == 0
