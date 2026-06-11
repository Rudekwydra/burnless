from __future__ import annotations

import json
import os
import subprocess
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


def test_session_seed_hook_no_capsule(tmp_path):
    """Test: no capsule on disk -> hook emits nothing."""
    home = tmp_path / "home"
    home.mkdir()

    stdout = _run_seed_hook(
        home,
        {
            "session_id": "sess-test",
            "cwd": str(tmp_path),
            "source": "direct",
        },
    )

    # Empty stdout means FAIL-OPEN (no capsule found).
    assert stdout.strip() == ""


def test_session_seed_hook_with_consolidated_capsule(tmp_path):
    """Test: consolidated rollover capsule exists -> hook emits JSON with seed message."""
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".burnless" / "state"
    state_dir.mkdir(parents=True)

    # Write a fake consolidated capsule.
    capsule_file = state_dir / "rollover-consolidated.md"
    capsule_content = "ESTADO X\nPedido: test\nResposta: ok"
    capsule_file.write_text(capsule_content, encoding="utf-8")

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


def test_session_seed_hook_with_session_rollover_capsule(tmp_path):
    """Test: session-*.rollover.md exists -> hook finds and emits it."""
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".burnless" / "state"
    state_dir.mkdir(parents=True)

    # Write a fake session rollover capsule.
    capsule_file = state_dir / "session-sess-test.rollover.md"
    capsule_content = "Pedido anterior: tarefa 1\nResposta: completo"
    capsule_file.write_text(capsule_content, encoding="utf-8")

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
    assert "Pedido anterior: tarefa 1" in ctx


def test_session_seed_hook_prefers_consolidated(tmp_path):
    """Test: consolidated exists and is non-empty -> prefer it over session-*.rollover.md."""
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".burnless" / "state"
    state_dir.mkdir(parents=True)

    # Write both consolidated and session rollover.
    consolidated = state_dir / "rollover-consolidated.md"
    consolidated.write_text("CONSOLIDATED ESTADO", encoding="utf-8")

    session_rollover = state_dir / "session-sess-test.rollover.md"
    session_rollover.write_text("SESSION ESTADO", encoding="utf-8")

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

    # Should use consolidated, not session rollover.
    assert "CONSOLIDATED ESTADO" in ctx
    assert "SESSION ESTADO" not in ctx
