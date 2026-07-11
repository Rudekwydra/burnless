"""Tests for status surface fixes: last_status persistence, broken metrics, false orphan alarms."""

import os
import time
from pathlib import Path

import pytest

from burnless import dashboard
from burnless import integrity


def test_runner_persists_last_status():
    """Verify runner.py contains st['last_status'] wiring and render_status displays it."""
    runner_path = Path(__file__).parent.parent / "src" / "burnless" / "exec" / "runner.py"
    runner_code = runner_path.read_text(encoding="utf-8")
    assert 'st["last_status"]' in runner_code, "runner.py must wire st['last_status'] in _persist_state"

    state = {"last_status": "OK:d842", "next": "x", "project": "P"}
    m = {"burnless_tokens": 1}
    output = dashboard.render_status(state, m)
    assert "OK:d842" in output, f"render_status output must contain 'OK:d842', got: {output}"


def test_render_status_no_broken_metric():
    """Verify render_status output does NOT contain 'Token Burn avoided' metric."""
    state = {"last_status": "OK:d001", "next": "", "project": "Test"}
    m = {"burnless_tokens": 100}
    output = dashboard.render_status(state, m)
    assert "Token Burn avoided" not in output, f"render_status must not contain 'Token Burn avoided', got: {output}"


def test_scan_orphans_skips_never_ran(tmp_path):
    """Verify scan_orphans ignores delegations with .md but no log (never ran)."""
    burnless_dir = tmp_path / ".burnless"
    deleg_dir = burnless_dir / "delegations"
    capsule_dir = burnless_dir / "capsules"
    logs_dir = burnless_dir / "logs"

    deleg_dir.mkdir(parents=True, exist_ok=True)
    capsule_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Create d001.md but no log, no capsule
    (deleg_dir / "d001.md").write_text("# d001\ntest", encoding="utf-8")

    result = integrity.scan_orphans(tmp_path, limit=50)
    assert result == [], f"Should skip delegations with no log, got: {result}"


def test_scan_orphans_skips_hot_run(tmp_path):
    """Verify scan_orphans skips hot runs (log mtime < 900s) and only returns truly orphaned old runs."""
    burnless_dir = tmp_path / ".burnless"
    deleg_dir = burnless_dir / "delegations"
    capsule_dir = burnless_dir / "capsules"
    logs_dir = burnless_dir / "logs"

    deleg_dir.mkdir(parents=True, exist_ok=True)
    capsule_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # d002.md + logs/d002.log created now (hot) → skip
    (deleg_dir / "d002.md").write_text("# d002\ntest", encoding="utf-8")
    (logs_dir / "d002.log").write_text("log", encoding="utf-8")

    # d003.md + logs/d003.log with old mtime, no capsule → include
    (deleg_dir / "d003.md").write_text("# d003\ntest", encoding="utf-8")
    old_log = logs_dir / "d003.log"
    old_log.write_text("old log", encoding="utf-8")
    old_time = time.time() - 2000  # 2000s in the past
    os.utime(old_log, (old_time, old_time))

    result = integrity.scan_orphans(tmp_path, limit=50)
    assert result == ["d003"], f"Should only return old orphans, got: {result}"
