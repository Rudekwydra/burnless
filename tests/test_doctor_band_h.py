"""Tests for burnless doctor band H (delegação/hook-guard)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from burnless import doctor


def test_h1_fail_when_filter_wired_and_jq_missing(tmp_path):
    """H1 should FAIL if delegation_filter.sh is wired but jq not in PATH."""
    home = tmp_path / "home"
    home.mkdir()
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    settings = {
        "hooks": {
            "UserPromptSubmit": [{
                "hooks": [{"type": "command",
                           "command": "bash ~/.claude/scripts/burnless_delegation_filter.sh",
                           "timeout": 3}],
            }],
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings))

    # Monkeypatch shutil.which to return None for jq
    with mock.patch("burnless.doctor.shutil.which") as mock_which:
        mock_which.return_value = None
        checks = doctor.run_checks(home=home, cwd=None)

    check_map = {c.id: c for c in checks}
    h1_check = check_map.get("H1")
    assert h1_check is not None, "H1 check not found"
    assert h1_check.status == "FAIL", f"H1 should FAIL, got {h1_check.status}"
    assert "jq" in h1_check.detail.lower() or "jq" in h1_check.fix_hint.lower()


def test_h1_pass_when_filter_not_wired(tmp_path):
    """H1 should PASS if delegation_filter.sh is not wired, even without jq."""
    home = tmp_path / "home"
    home.mkdir()
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    settings = {
        "hooks": {
            "UserPromptSubmit": [{
                "hooks": [{"type": "command",
                           "command": "bash ~/.claude/scripts/other_hook.sh",
                           "timeout": 3}],
            }],
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings))

    # Monkeypatch shutil.which to return None for jq
    with mock.patch("burnless.doctor.shutil.which") as mock_which:
        mock_which.return_value = None
        checks = doctor.run_checks(home=home, cwd=None)

    check_map = {c.id: c for c in checks}
    h1_check = check_map.get("H1")
    assert h1_check is not None, "H1 check not found"
    assert h1_check.status == "PASS", f"H1 should PASS, got {h1_check.status}"


def test_h2_fail_on_missing_hook_script(tmp_path):
    """H2 should FAIL if hook script referenced in settings.json does not exist."""
    home = tmp_path / "home"
    home.mkdir()
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    missing_script = home / ".claude" / "scripts" / "missing_hook.sh"
    settings = {
        "hooks": {
            "UserPromptSubmit": [{
                "hooks": [{"type": "command",
                           "command": f"bash {missing_script}",
                           "timeout": 3}],
            }],
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings))

    checks = doctor.run_checks(home=home, cwd=None)

    check_map = {c.id: c for c in checks}
    h2_check = check_map.get("H2")
    assert h2_check is not None, "H2 check not found"
    assert h2_check.status == "FAIL", f"H2 should FAIL, got {h2_check.status}"
    assert str(missing_script) in h2_check.detail or str(missing_script) in h2_check.fix_hint


def test_fix_mcp_includes_server_command():
    """_fix_mcp should include sys.executable, -m, and burnless.mcp_server in the command."""
    # Patch subprocess.run to avoid actually running the command
    with mock.patch("burnless.doctor.subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(returncode=0, stdout="ok", stderr="")

        # Find the _check_d function and extract _fix_mcp
        checks = []
        doctor._check_d(checks)

        # Now get the fixer from D2 check
        d2_check = [c for c in checks if c.id == "D2"][0]
        if d2_check.fixer is not None:
            d2_check.fixer()

            # Find the call that matches our MCP add command (last call should be the fixer)
            # Filter for calls with "add" command
            add_calls = [
                call for call in mock_run.call_args_list
                if call[0] and "add" in call[0][0]
            ]
            assert len(add_calls) >= 1, f"No 'add' command found in calls: {mock_run.call_args_list}"

            # Get the last add call (from fixer)
            cmd = add_calls[-1][0][0]

            assert "claude" in cmd, f"Expected 'claude' in cmd, got {cmd}"
            assert "mcp" in cmd, f"Expected 'mcp' in cmd, got {cmd}"
            assert "add" in cmd, f"Expected 'add' in cmd, got {cmd}"
            assert "burnless" in cmd, f"Expected 'burnless' in cmd, got {cmd}"
            assert "--" in cmd, f"Expected '--' separator in cmd, got {cmd}"
            assert "-m" in cmd, f"Expected '-m' in cmd, got {cmd}"
            assert "burnless.mcp_server" in cmd, f"Expected 'burnless.mcp_server' in cmd, got {cmd}"
            assert sys.executable in cmd, f"Expected sys.executable in cmd, got {cmd}"
