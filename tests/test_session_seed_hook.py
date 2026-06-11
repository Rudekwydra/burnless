from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


HOOK = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "burnless_session_seed.sh"


def _run_seed_hook(home: Path, payload: dict) -> str:
    """Run the SessionStart seed hook and return stdout."""
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
    return proc.stdout


def test_session_seed_hook_no_pointer(tmp_path):
    """Test: no pending_seed.md pointer -> hook emits nothing."""
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".burnless" / "state"
    state_dir.mkdir(parents=True)

    stdout = _run_seed_hook(
        home,
        {
            "session_id": "sess-test",
            "cwd": str(tmp_path),
            "source": "direct",
        },
    )

    # Empty stdout means FAIL-OPEN (no pointer found).
    assert stdout.strip() == ""


def test_session_seed_hook_fresh_pointer(tmp_path):
    """Test: fresh pending_seed.md pointer -> hook emits JSON with content."""
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".burnless" / "state"
    state_dir.mkdir(parents=True)

    # Write pending_seed.md with fresh mtime.
    pointer_file = state_dir / "pending_seed.md"
    pointer_file.write_text("ESTADO X\nPedido: test", encoding="utf-8")

    stdout = _run_seed_hook(
        home,
        {
            "session_id": "sess-test",
            "cwd": str(tmp_path),
            "source": "direct",
        },
    )

    assert stdout.strip(), "Expected JSON output"
    output = json.loads(stdout)
    ctx = output["hookSpecificOutput"]["additionalContext"]

    # Check seed message and content are present.
    assert "[BURNLESS SEED]" in ctx
    assert "sessao iniciada leve a partir da capsule rolante" in ctx
    assert "ESTADO X" in ctx


def test_session_seed_hook_stale_pointer(tmp_path):
    """Test: stale pending_seed.md (>24h old) -> hook emits nothing and removes file."""
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".burnless" / "state"
    state_dir.mkdir(parents=True)

    # Write pending_seed.md with stale mtime (2 days ago).
    pointer_file = state_dir / "pending_seed.md"
    pointer_file.write_text("STALE ESTADO", encoding="utf-8")

    # Set mtime to 2 days ago.
    two_days_ago = time.time() - (2 * 86400)
    os.utime(str(pointer_file), (two_days_ago, two_days_ago))

    stdout = _run_seed_hook(
        home,
        {
            "session_id": "sess-test",
            "cwd": str(tmp_path),
            "source": "direct",
        },
    )

    # Should emit nothing.
    assert stdout.strip() == ""

    # File should be removed.
    assert not pointer_file.exists(), "Stale pointer file should be removed"
