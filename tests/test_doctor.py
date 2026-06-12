"""Tests for burnless doctor healthcheck."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from burnless import doctor
from burnless.init_claude_code import (
    _MANAGED, is_wired, wire_settings_hook, _resolve_templates_dir,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_burnless_root(base: Path) -> Path:
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


# ── (a) green: fully-wired tmp HOME → no FAIL in A/B/C ───────────────────────

def test_green_no_fail_in_abc(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_burnless_root(proj)
    _wire_home(home)
    _install_managed_files(home)

    checks = doctor.run_checks(home=home, cwd=proj)

    abc_fails = [c for c in checks if c.band in ("A", "B", "C") and c.status == "FAIL"]
    assert abc_fails == [], f"Unexpected FAIL in A/B/C: {abc_fails}"
    assert doctor.exit_code(checks) == 0


# ── (b) broken: empty HOME → B1/C1/C2/C3 FAIL, exit==1 ──────────────────────

def test_broken_empty_home(tmp_path):
    home = tmp_path / "empty_home"
    home.mkdir()
    cwd = tmp_path / "no_burnless"
    cwd.mkdir()

    checks = doctor.run_checks(home=home, cwd=cwd)

    check_map = {c.id: c for c in checks}
    assert check_map["B1"].status == "FAIL", "B1 should FAIL with no .burnless/"
    assert check_map["C1"].status == "FAIL", "C1 should FAIL with no settings.json"
    assert check_map["C2"].status == "FAIL", "C2 should FAIL with no settings.json"
    assert check_map["C3"].status == "FAIL", "C3 should FAIL with no hooks"
    assert doctor.exit_code(checks) == 1


# ── (c) render_json keys stable ──────────────────────────────────────────────

def test_render_json_keys_stable(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    checks = doctor.run_checks(home=home, cwd=tmp_path)

    result = doctor.render_json(checks)
    assert set(result) >= {"version", "checks", "summary", "exit"}, (
        f"render_json missing required keys; got {set(result)}"
    )
    assert isinstance(result["checks"], list)
    assert isinstance(result["summary"], dict)
    assert set(result["summary"]) >= {"pass", "warn", "fail"}
    assert result["exit"] in (0, 1)


# ── (d) is_wired idempotency preserved ───────────────────────────────────────

def test_is_wired_idempotency(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    # Initially not wired
    initial = is_wired(home)
    assert not initial["settings_exists"]
    assert not initial["userprompt"]
    assert not initial["sessionstart"]

    # Create empty settings.json; wire once
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "settings.json").write_text("{}")
    status1 = wire_settings_hook(home)
    assert status1 == "wired", f"Expected 'wired', got {status1!r}"

    # Wire again — must be idempotent
    status2 = wire_settings_hook(home)
    assert status2 == "already-wired", f"Expected 'already-wired', got {status2!r}"

    # is_wired must detect both hooks
    after = is_wired(home)
    assert after["settings_exists"]
    assert after["settings_parses"]
    assert after["userprompt"], "userprompt hook not detected after wire"
    assert after["sessionstart"], "sessionstart hook not detected after wire"

    # Third wire must still be idempotent (settings unchanged)
    data_before = json.loads((home / ".claude" / "settings.json").read_text())
    status3 = wire_settings_hook(home)
    assert status3 == "already-wired"
    data_after = json.loads((home / ".claude" / "settings.json").read_text())
    # hooks count must not grow
    n_ups_before = len(data_before["hooks"]["UserPromptSubmit"])
    n_ups_after  = len(data_after["hooks"]["UserPromptSubmit"])
    assert n_ups_before == n_ups_after, "UserPromptSubmit hooks grew on re-wire"


# ── (e) mcp_server --check rc==0 ─────────────────────────────────────────────

def test_mcp_server_check_rc():
    pytest.importorskip("mcp")
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "burnless.mcp_server", "--check"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"mcp_server --check returned {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "ok" in result.stdout.lower(), f"Expected 'ok' in stdout: {result.stdout!r}"
