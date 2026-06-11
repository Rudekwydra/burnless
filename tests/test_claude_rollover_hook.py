from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


HOOK = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "burnless_mode_hook.sh"


def _run_hook(home: Path, payload: dict, *, rollover_turns: int = 2) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["BURNLESS_ROLLOVER_TURNS"] = str(rollover_turns)
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


def test_rollover_mode_creates_and_reuses_capsule(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    transcript = home / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"role": "user", "content": "first goal"}}),
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "first reply"}}),
                json.dumps({"type": "user", "message": {"role": "user", "content": "second goal"}}),
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "second reply"}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    activate = _run_hook(
        home,
        {
            "session_id": "sess-1",
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "/burnless rollover",
        },
    )
    assert "Burnless mode -> rollover" in activate["hookSpecificOutput"]["additionalContext"]

    mode_file = home / ".burnless" / "state" / "session-sess-1.mode"
    assert mode_file.read_text(encoding="utf-8").strip() == "rollover"

    rollover = _run_hook(
        home,
        {
            "session_id": "sess-1",
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "third goal",
        },
    )

    ctx = rollover["hookSpecificOutput"]["additionalContext"]
    assert "[BURNLESS ROLLOVER]" in ctx
    assert "third goal" in ctx
    assert "second reply" in ctx

    capsule_path = home / ".burnless" / "state" / "session-sess-1.rollover.md"
    meta_path = home / ".burnless" / "state" / "session-sess-1.rollover.json"
    assert capsule_path.exists()
    assert meta_path.exists()
