from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "restart_rollover.sh"


def test_restart_rollover_dry_run_promotes_latest_capsule(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    target_dir = home / "antigravity" / "burnless"
    target_dir.mkdir(parents=True)

    project_dir = home / ".claude" / "projects" / target_dir.as_posix().replace("/", "-")
    project_dir.mkdir(parents=True)

    session_id = "session-abc123"
    transcript = project_dir / f"{session_id}.jsonl"
    transcript.write_text("{}", encoding="utf-8")
    transcript.touch()

    state_dir = home / ".burnless" / "state"
    state_dir.mkdir(parents=True)
    capsule = state_dir / f"session-{session_id}.rollover.md"
    capsule.write_text("capsule body", encoding="utf-8")

    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"hooks":{"UserPromptSubmit":[{"hooks":[{"command":"bash ~/.claude/scripts/burnless_mode_hook.sh"}]}]}}', encoding="utf-8")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["BURNLESS_ROLLOVER_DRYRUN"] = "1"
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "seed promovido" in proc.stdout
    content = (state_dir / "pending_seed.md").read_text(encoding="utf-8")
    assert content == f"<!-- burnless-seed-target: {target_dir} -->\ncapsule body"
