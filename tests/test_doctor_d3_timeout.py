"""Tests for burnless doctor D3 timeout configuration."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
import yaml

from burnless import doctor
from burnless.init_claude_code import _MANAGED, is_wired, wire_settings_hook, _resolve_templates_dir


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_burnless_root(base: Path, epochs_override: dict | None = None) -> Path:
    """Create a minimal .burnless/ with valid config and state."""
    bl = base / ".burnless"
    bl.mkdir(parents=True, exist_ok=True)
    cfg = {
        "agents": {
            "bronze": {"name": "haiku", "command": "claude --model haiku -p"},
            "silver": {"name": "sonnet", "command": "claude --model sonnet -p"},
        },
        "routing": {"bronze": ["summarize"], "silver": ["code", "bug"]},
        "metrics": {"token_estimation_ratio": 4},
    }
    if epochs_override is not None:
        cfg["epochs"] = epochs_override
    (bl / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    (bl / "state.json").write_text("{}")
    return bl


def _wire_home(home: Path) -> None:
    """Write settings.json with both hooks wired."""
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "hooks": {
            "UserPromptSubmit": [{
                "hooks": [{"type": "command",
                           "command": "bash ~/.claude/scripts/burnless_mode_hook.sh",
                           "timeout": 3}],
            }],
            "SessionStart": [{
                "hooks": [{"type": "command",
                           "command": "bash ~/.claude/scripts/burnless_session_seed.sh",
                           "timeout": 10}],
            }],
            "SessionEnd": [{
                "hooks": [{"type": "command",
                           "command": "bash ~/.claude/scripts/burnless_epoch_end.sh",
                           "timeout": 10}],
            }],
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings))


def _install_managed_files(home: Path) -> None:
    """Create managed file stubs in home; copy from templates if available."""
    tdir = _resolve_templates_dir()
    for src_rel, dst_rel in _MANAGED:
        dst = home / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if tdir is not None:
            src = tdir / src_rel
            if src.exists():
                dst.write_bytes(src.read_bytes())
                continue
        dst.write_text("# burnless managed stub\n")


# ── tests ─────────────────────────────────────────────────────────────────────

def test_d3_default_timeout_is_10(tmp_path):
    """D3: default timeout is 10s when key not in config."""
    import subprocess

    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_burnless_root(proj)
    _wire_home(home)
    _install_managed_files(home)

    def mock_subprocess_run(*args, **kwargs):
        timeout = kwargs.get("timeout", 10)
        raise subprocess.TimeoutExpired(["claude", "mcp", "list"], timeout)

    with mock.patch("burnless.doctor.subprocess.run", side_effect=mock_subprocess_run):
        checks = doctor.run_checks(home=home, cwd=proj)
        d3_check = next((c for c in checks if c.id == "D3"), None)

        assert d3_check is not None, "D3 check not found"
        assert d3_check.status == "WARN"
        assert "timed out (10s)" in d3_check.detail


def test_d3_config_timeout_respected(tmp_path):
    """D3: config value mcp_list_timeout_s is respected when present."""
    import subprocess

    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_burnless_root(proj, epochs_override={"mcp_list_timeout_s": 15})
    _wire_home(home)
    _install_managed_files(home)

    def mock_subprocess_run(*args, **kwargs):
        timeout = kwargs.get("timeout", 10)
        raise subprocess.TimeoutExpired(["claude", "mcp", "list"], timeout)

    with mock.patch("burnless.doctor.subprocess.run", side_effect=mock_subprocess_run):
        checks = doctor.run_checks(home=home, cwd=proj)
        d3_check = next((c for c in checks if c.id == "D3"), None)

        assert d3_check is not None, "D3 check not found"
        assert d3_check.status == "WARN"
        assert "timed out (15s)" in d3_check.detail


def test_d3_timeout_message_no_hardcoded_3s(tmp_path):
    """D3: timeout message does not contain hardcoded (3s) literal."""
    import subprocess

    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_burnless_root(proj)
    _wire_home(home)
    _install_managed_files(home)

    def mock_subprocess_run(*args, **kwargs):
        timeout = kwargs.get("timeout", 10)
        raise subprocess.TimeoutExpired(["claude", "mcp", "list"], timeout)

    with mock.patch("burnless.doctor.subprocess.run", side_effect=mock_subprocess_run):
        checks = doctor.run_checks(home=home, cwd=proj)
        d3_check = next((c for c in checks if c.id == "D3"), None)

        assert d3_check is not None, "D3 check not found"
        assert "(3s)" not in d3_check.detail, "D3 message should not contain hardcoded (3s)"
