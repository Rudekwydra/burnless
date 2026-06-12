from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

SEED_SCRIPT = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "burnless_session_seed.sh"


def _run_seed(home: Path, cwd: str) -> str:
    env = os.environ.copy()
    env["HOME"] = str(home)
    proc = subprocess.run(
        ["bash", str(SEED_SCRIPT)],
        input=json.dumps({"cwd": cwd, "hook_event_name": "SessionStart"}),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    return proc.stdout


def test_consume_once(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".burnless" / "state"
    state_dir.mkdir(parents=True)
    seed = state_dir / "pending_seed.md"
    seed.write_text(
        "<!-- burnless-seed-target: /proj/a -->\nsome rollup content\n",
        encoding="utf-8",
    )

    out = _run_seed(home, "/proj/a")
    assert out.strip(), "should emit additionalContext"
    data = json.loads(out)
    ctx = data["hookSpecificOutput"]["additionalContext"]
    assert "some rollup content" in ctx
    assert "burnless-seed-target" not in ctx, "marker line must be stripped from output"
    assert not seed.exists(), "pending_seed.md must be deleted after emit"

    out2 = _run_seed(home, "/proj/a")
    assert not out2.strip(), "second call must emit nothing (already consumed)"


def test_scope_mismatch(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".burnless" / "state"
    state_dir.mkdir(parents=True)
    seed = state_dir / "pending_seed.md"
    seed.write_text(
        "<!-- burnless-seed-target: /proj/a -->\nsome rollup content\n",
        encoding="utf-8",
    )

    out = _run_seed(home, "/proj/b")
    assert not out.strip(), "scope mismatch must produce no output"
    assert seed.exists(), "pending_seed.md must be preserved on scope mismatch"


def test_back_compat(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".burnless" / "state"
    state_dir.mkdir(parents=True)
    seed = state_dir / "pending_seed.md"
    seed.write_text("no marker here\njust content\n", encoding="utf-8")

    out = _run_seed(home, "/any/cwd")
    assert out.strip(), "back-compat: must emit even without marker"
    data = json.loads(out)
    ctx = data["hookSpecificOutput"]["additionalContext"]
    assert "just content" in ctx
    assert not seed.exists(), "back-compat: pending_seed.md must be deleted after emit"
