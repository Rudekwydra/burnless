"""Tests for doctor C9 check with time window filtering (d024)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from burnless import doctor


@pytest.fixture
def tmp_home(tmp_path):
    """Isolated tmp home directory."""
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def tmp_project(tmp_path):
    """Isolated tmp project with .burnless/ directory."""
    proj = tmp_path / "project"
    proj.mkdir()
    burnless_dir = proj / ".burnless"
    burnless_dir.mkdir()
    return proj


def _write_hook_error_log(home: Path, entries: list[dict]) -> Path:
    """Write JSON-formatted hook error log entries."""
    state_dir = home / ".burnless" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "hook_errors.log"

    lines = [json.dumps(e) for e in entries]
    log_path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    return log_path


def test_c9_old_entries_only_pass(tmp_project, tmp_home, monkeypatch):
    """Test C9 PASS when log has only old entries (ts > 30d ago)."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    # Create hook error from 30 days ago
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    entries = [{"schema": 1, "ts": old_ts, "hook": "test", "error": "old error"}]
    _write_hook_error_log(tmp_home, entries)

    # Config with 24h window
    config_path = tmp_project / ".burnless" / "config.yaml"
    config_path.write_text("epochs:\n  hook_error_tail: 5\n  hook_error_window_h: 24\n")

    checks = doctor.run_checks(home=tmp_home, cwd=tmp_project, fix=False)
    c9 = [c for c in checks if c.id == "C9"][0]

    assert c9.status == "PASS"
    assert "no hook errors in the last 24h" in c9.detail
    assert "1 older entries ignored" in c9.detail


def test_c9_recent_entries_warn(tmp_project, tmp_home, monkeypatch):
    """Test C9 WARN when log has at least one recent entry."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    # Create hook error from 1 hour ago (within 24h window)
    recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    entries = [{"schema": 1, "ts": recent_ts, "hook": "UserPromptSubmit", "error": "recent error"}]
    _write_hook_error_log(tmp_home, entries)

    config_path = tmp_project / ".burnless" / "config.yaml"
    config_path.write_text("epochs:\n  hook_error_tail: 5\n  hook_error_window_h: 24\n")

    checks = doctor.run_checks(home=tmp_home, cwd=tmp_project, fix=False)
    c9 = [c for c in checks if c.id == "C9"][0]

    assert c9.status == "WARN"
    assert "hook errors recorded:" in c9.detail


def test_c9_corrupted_json_is_recent(tmp_project, tmp_home, monkeypatch):
    """Test C9 WARN when log has unparseable JSON (fail-safe: treat as recent)."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    state_dir = tmp_home / ".burnless" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "hook_errors.log"
    log_path.write_text("not valid json\n", encoding="utf-8")

    config_path = tmp_project / ".burnless" / "config.yaml"
    config_path.write_text("epochs:\n  hook_error_tail: 5\n  hook_error_window_h: 24\n")

    checks = doctor.run_checks(home=tmp_home, cwd=tmp_project, fix=False)
    c9 = [c for c in checks if c.id == "C9"][0]

    assert c9.status == "WARN", "Corrupted JSON should be treated as recent"
    assert "hook errors recorded:" in c9.detail


def test_c9_empty_log_pass(tmp_project, tmp_home, monkeypatch):
    """Test C9 PASS when log is empty."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    _write_hook_error_log(tmp_home, [])

    config_path = tmp_project / ".burnless" / "config.yaml"
    config_path.write_text("epochs:\n  hook_error_tail: 5\n  hook_error_window_h: 24\n")

    checks = doctor.run_checks(home=tmp_home, cwd=tmp_project, fix=False)
    c9 = [c for c in checks if c.id == "C9"][0]

    assert c9.status == "PASS"
    assert "empty" in c9.detail.lower()


def test_c9_window_disabled_with_zero(tmp_project, tmp_home, monkeypatch):
    """Test C9 treats all entries as recent when window_h=0 (no cutoff)."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    # Old entry from 30 days ago
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    entries = [{"schema": 1, "ts": old_ts, "hook": "test", "error": "very old"}]
    _write_hook_error_log(tmp_home, entries)

    # Config with window disabled (0) — mock load to ensure it's used
    config_path = tmp_project / ".burnless" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("epochs:\n  hook_error_tail: 5\n  hook_error_window_h: 0\n")

    # Mock config_mod.load to return our config
    from burnless import config as config_mod
    original_load = config_mod.load

    def mock_load(path):
        return {"epochs": {"hook_error_tail": 5, "hook_error_window_h": 0}}

    monkeypatch.setattr(config_mod, "load", mock_load)

    checks = doctor.run_checks(home=tmp_home, cwd=tmp_project, fix=False)
    c9 = [c for c in checks if c.id == "C9"][0]

    assert c9.status == "WARN", "With window_h=0, no cutoff — all entries recent, should WARN"
    assert "hook errors recorded:" in c9.detail


def test_c9_mixed_old_and_recent(tmp_project, tmp_home, monkeypatch):
    """Test C9 shows WARN with only recent entries when both old and recent exist."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    recent_ts = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    entries = [
        {"schema": 1, "ts": old_ts, "hook": "test", "error": "old error 1"},
        {"schema": 1, "ts": old_ts, "hook": "test", "error": "old error 2"},
        {"schema": 1, "ts": recent_ts, "hook": "UserPromptSubmit", "error": "recent error"},
    ]
    _write_hook_error_log(tmp_home, entries)

    config_path = tmp_project / ".burnless" / "config.yaml"
    config_path.write_text("epochs:\n  hook_error_tail: 5\n  hook_error_window_h: 24\n")

    checks = doctor.run_checks(home=tmp_home, cwd=tmp_project, fix=False)
    c9 = [c for c in checks if c.id == "C9"][0]

    assert c9.status == "WARN"
    assert "hook errors recorded:" in c9.detail
    # Should show recent error but not the old ones
    assert "recent error" in c9.detail
