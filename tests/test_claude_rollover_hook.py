from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


HOOK = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "burnless_mode_hook.sh"


def _run_hook(home: Path, payload: dict) -> dict:
    """Run the hook expecting injected context (non-empty JSON stdout)."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), proc.stderr
    return json.loads(proc.stdout)


def _run_hook_raw(home: Path, payload: dict) -> subprocess.CompletedProcess:
    """Run the hook allowing empty stdout (e.g. off / no-op)."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def _mode_file(home: Path, sid: str) -> Path:
    return home / ".burnless" / "state" / f"session-{sid}.mode"


def _no_rollover_artifacts(home: Path, sid: str) -> None:
    state = home / ".burnless" / "state"
    assert not (state / f"session-{sid}.rollover.md").exists()
    assert not (state / f"session-{sid}.rollover.json").exists()
    assert not (state / f"session-{sid}.seed.md").exists()
    assert not (state / "rotation_due").exists()


# ---- legacy alias coercion via /burnless command -------------------------

def test_command_rollover_coerced_to_on(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = _run_hook(
        home,
        {"session_id": "sess-1", "hook_event_name": "UserPromptSubmit", "prompt": "/burnless rollover"},
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    # legacy alias is acknowledged as deprecated and mapped to canonical 'on'
    assert "deprecated" in ctx.lower()
    assert "on" in ctx.lower()
    # persisted value is canonical, never the legacy string
    assert _mode_file(home, "sess-1").read_text(encoding="utf-8").strip() == "on"


def test_command_partner_coerced_to_observe(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = _run_hook(
        home,
        {"session_id": "sess-2", "hook_event_name": "UserPromptSubmit", "prompt": "/burnless partner"},
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "deprecated" in ctx.lower()
    assert _mode_file(home, "sess-2").read_text(encoding="utf-8").strip() == "observe"


# ---- canonical command sets ----------------------------------------------

def test_command_sets_observe(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = _run_hook(
        home,
        {"session_id": "s", "hook_event_name": "UserPromptSubmit", "prompt": "/burnless observe"},
    )
    assert "observe" in out["hookSpecificOutput"]["additionalContext"].lower()
    assert _mode_file(home, "s").read_text(encoding="utf-8").strip() == "observe"


# ---- runtime dispatch per mode -------------------------------------------

def test_on_mode_injects_maestro(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    mf = _mode_file(home, "on-sid")
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_text("on", encoding="utf-8")
    out = _run_hook(
        home,
        {"session_id": "on-sid", "hook_event_name": "UserPromptSubmit", "prompt": "do a thing"},
    )
    assert "[BURNLESS ON]" in out["hookSpecificOutput"]["additionalContext"]
    _no_rollover_artifacts(home, "on-sid")


def test_observe_mode_injects_note(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    mf = _mode_file(home, "obs-sid")
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_text("observe", encoding="utf-8")
    out = _run_hook(
        home,
        {"session_id": "obs-sid", "hook_event_name": "UserPromptSubmit", "prompt": "do a thing"},
    )
    assert "[BURNLESS OBSERVE]" in out["hookSpecificOutput"]["additionalContext"]
    _no_rollover_artifacts(home, "obs-sid")


def test_off_mode_is_noop(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proc = _run_hook_raw(
        home,
        {"session_id": "off-sid", "hook_event_name": "UserPromptSubmit", "prompt": "do a thing"},
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# ---- persisted legacy value migrates on read -----------------------------

def test_persisted_rollover_migrates_to_on(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    mf = _mode_file(home, "mig-sid")
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_text("rollover", encoding="utf-8")  # legacy value on disk
    out = _run_hook(
        home,
        {"session_id": "mig-sid", "hook_event_name": "UserPromptSubmit", "prompt": "next prompt"},
    )
    # coerced to 'on' behavior...
    assert "[BURNLESS ON]" in out["hookSpecificOutput"]["additionalContext"]
    # ...and the file is migrated to canonical, no legacy value left
    assert mf.read_text(encoding="utf-8").strip() == "on"
    _no_rollover_artifacts(home, "mig-sid")


def test_on_mode_survives_clear_via_project_fallback(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PWD"] = str(project)
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"session_id": "sid-a", "hook_event_name": "UserPromptSubmit", "prompt": "/burnless on"}),
        text=True,
        capture_output=True,
        env=env,
        cwd=project,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert _mode_file(home, "sid-a").read_text(encoding="utf-8").strip() == "on"
    assert (home / ".burnless" / "state" / "last-project.mode").read_text(encoding="utf-8").strip() == "on"

    env2 = os.environ.copy()
    env2["HOME"] = str(home)
    env2["PWD"] = str(project)
    proc2 = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"session_id": "sid-b", "hook_event_name": "UserPromptSubmit", "prompt": "do a thing"}),
        text=True,
        capture_output=True,
        env=env2,
        cwd=project,
        check=False,
    )
    assert proc2.returncode == 0, proc2.stderr
    assert "[BURNLESS ON]" in json.loads(proc2.stdout)["hookSpecificOutput"]["additionalContext"]
    assert not _mode_file(home, "sid-b").exists()


# ---- menu lists only canonical modes -------------------------------------

def test_menu_lists_canonical_modes(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    out = _run_hook(
        home,
        {"session_id": "menu-sid", "hook_event_name": "UserPromptSubmit", "prompt": "/burnless"},
    )
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "/burnless on" in ctx
    assert "/burnless observe" in ctx
    assert "/burnless off" in ctx
