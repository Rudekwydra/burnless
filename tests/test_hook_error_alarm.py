"""Tests for hook error log rotation and doctor watermark/error alarms (d816)."""
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from burnless import recovery, doctor
from burnless.doctor import run_checks


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


def test_record_hook_error_writes_json_line(tmp_home, monkeypatch):
    """Test that record_hook_error writes a JSON line with hook and error fields."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    root = tmp_home / ".burnless" / "state"
    root.mkdir(parents=True, exist_ok=True)

    payload = recovery.record_hook_error(
        root,
        hook="test_hook",
        host="test_host",
        error="test error message",
    )

    assert payload["hook"] == "test_hook"
    assert payload["error"] == "test error message"
    assert payload["schema"] == 1

    log_path = tmp_home / ".burnless" / "state" / "hook_errors.log"
    assert log_path.exists()

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["hook"] == "test_hook"
    assert parsed["error"] == "test error message"


def test_hook_error_log_rotation_at_1mib(tmp_home, monkeypatch):
    """Test that log rotates to .1 when exceeding 1 MiB."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    root = tmp_home / ".burnless" / "state"
    root.mkdir(parents=True, exist_ok=True)

    log_path = tmp_home / ".burnless" / "state" / "hook_errors.log"
    log_rotated = log_path.with_suffix(log_path.suffix + ".1")

    # Write a large fake log (>1 MiB)
    large_line = json.dumps({
        "schema": 1,
        "ts": "2026-07-06T00:00:00Z",
        "hook": "test",
        "host": "test",
        "error": "x" * 10000
    }) + "\n"
    num_lines = int(1048576 / len(large_line)) + 10
    log_path.write_text(large_line * num_lines, encoding="utf-8")
    initial_size = log_path.stat().st_size
    assert initial_size > 1048576

    # Record another error — should trigger rotation
    recovery.record_hook_error(
        root,
        hook="after_rotation",
        host="test_host",
        error="error after rotation",
    )

    # Verify rotation happened
    assert log_rotated.exists(), "rotated log .1 file should exist"
    rotated_size = log_rotated.stat().st_size
    assert rotated_size == initial_size

    # Verify live log is now small
    live_size = log_path.stat().st_size
    assert live_size < 1000  # One new JSON line

    # Verify the new entry is in the live log
    new_line = log_path.read_text(encoding="utf-8").strip()
    parsed = json.loads(new_line)
    assert parsed["hook"] == "after_rotation"


def test_doctor_c8_watermark_threshold_warn(tmp_project, tmp_home, monkeypatch):
    """Test C8 emits WARN when watermark gap >= threshold."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    # Write a minimal config with watermark_alarm_gap=3
    config_path = tmp_project / ".burnless" / "config.yaml"
    config_path.write_text(
        "epochs:\n  watermark_alarm_gap: 3\n",
        encoding="utf-8"
    )

    # Mock summarize_session_log to return gap=5 (>= threshold 3)
    with patch("burnless.pilot.summarize_session_log") as mock_log:
        mock_log.return_value = {"watermark_gap": 5, "last_error": None}
        checks = run_checks(home=tmp_home, cwd=tmp_project, fix=False)

    c8_checks = [c for c in checks if c.id == "C8"]
    assert len(c8_checks) == 1
    c8 = c8_checks[0]
    assert c8.status == "WARN"
    assert "watermark gap: 5" in c8.detail


def test_doctor_c8_watermark_threshold_pass(tmp_project, tmp_home, monkeypatch):
    """Test C8 emits PASS when 0 < gap < threshold."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    # Write config with watermark_alarm_gap=5
    config_path = tmp_project / ".burnless" / "config.yaml"
    config_path.write_text(
        "epochs:\n  watermark_alarm_gap: 5\n",
        encoding="utf-8"
    )

    # Mock summarize_session_log to return gap=2 (< threshold 5)
    with patch("burnless.pilot.summarize_session_log") as mock_log:
        mock_log.return_value = {"watermark_gap": 2, "last_error": None}
        checks = run_checks(home=tmp_home, cwd=tmp_project, fix=False)

    c8_checks = [c for c in checks if c.id == "C8"]
    assert len(c8_checks) == 1
    c8 = c8_checks[0]
    assert c8.status == "PASS"
    assert "watermark gap: 2" in c8.detail


def test_doctor_c9_hook_error_visibility(tmp_project, tmp_home, monkeypatch):
    """Test C9 shows hook errors recorded from isolated home."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    # Create state dir and write a hook error log
    state_dir = tmp_home / ".burnless" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "hook_errors.log"

    hook_error = {
        "schema": 1,
        "ts": "2026-07-06T00:00:00Z",
        "hook": "UserPromptSubmit",
        "host": "test_host",
        "error": "test error for C9"
    }
    log_path.write_text(json.dumps(hook_error) + "\n", encoding="utf-8")

    # Write config with hook_error_tail
    config_path = tmp_project / ".burnless" / "config.yaml"
    config_path.write_text(
        "epochs:\n  hook_error_tail: 5\n",
        encoding="utf-8"
    )

    checks = run_checks(home=tmp_home, cwd=tmp_project, fix=False)

    c9_checks = [c for c in checks if c.id == "C9"]
    assert len(c9_checks) == 1
    c9 = c9_checks[0]
    assert c9.status == "WARN"
    assert "hook errors recorded:" in c9.detail


def test_doctor_c9_shows_multiple_tail_lines(tmp_project, tmp_home, monkeypatch):
    """Test C9 shows multiple tail lines up to hook_error_tail limit."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    # Create state dir and write multiple hook error lines
    state_dir = tmp_home / ".burnless" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "hook_errors.log"

    lines = []
    for i in range(7):
        error = {
            "schema": 1,
            "ts": "2026-07-06T00:00:00Z",
            "hook": "UserPromptSubmit",
            "host": "test_host",
            "error": f"error_{i}"
        }
        lines.append(json.dumps(error))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Write config with hook_error_tail=3 (should show last 3)
    config_path = tmp_project / ".burnless" / "config.yaml"
    config_path.write_text(
        "epochs:\n  hook_error_tail: 3\n",
        encoding="utf-8"
    )

    checks = run_checks(home=tmp_home, cwd=tmp_project, fix=False)

    c9_checks = [c for c in checks if c.id == "C9"]
    assert len(c9_checks) == 1
    c9 = c9_checks[0]
    assert c9.status == "WARN"
    assert "hook errors recorded:" in c9.detail
    # Last 3 errors should be in detail
    assert "error_4" in c9.detail or "error_5" in c9.detail or "error_6" in c9.detail


def test_doctor_c9_empty_log(tmp_project, tmp_home, monkeypatch):
    """Test C9 shows PASS when log is empty."""
    monkeypatch.setenv("HOME", str(tmp_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_home)

    # Create state dir and empty hook error log
    state_dir = tmp_home / ".burnless" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "hook_errors.log"
    log_path.write_text("", encoding="utf-8")

    # Write config
    config_path = tmp_project / ".burnless" / "config.yaml"
    config_path.write_text(
        "epochs:\n  hook_error_tail: 5\n",
        encoding="utf-8"
    )

    checks = run_checks(home=tmp_home, cwd=tmp_project, fix=False)

    c9_checks = [c for c in checks if c.id == "C9"]
    assert len(c9_checks) == 1
    c9 = c9_checks[0]
    assert c9.status == "PASS"
    assert "empty" in c9.detail.lower()
