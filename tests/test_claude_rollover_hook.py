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


def test_seed_survives_rewind(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    sid = "sess-rewind"

    # Transcript: 1 user + 1 assistant; prompt will be 2nd user → turns=2, 2%2==0 → rotation
    transcript = home / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "first message"}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "first reply"}}) + "\n",
        encoding="utf-8",
    )

    # Activate rollover mode
    _run_hook(
        home,
        {
            "session_id": sid,
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "/burnless rollover",
        },
        rollover_turns=2,
    )

    # Run at rotation point: transcript has 1 user turn, prompt adds 2nd → turns=2 → seed written
    _run_hook(
        home,
        {
            "session_id": sid,
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "second user turn",
        },
        rollover_turns=2,
    )

    state_dir = home / ".burnless" / "state"
    seed_path = state_dir / f"session-{sid}.seed.md"
    assert seed_path.exists(), "seed.md must exist after rotation point"
    seed_content = seed_path.read_text(encoding="utf-8")
    assert seed_content.strip(), "seed.md must not be empty"

    # Simulate /rewind: empty transcript, same session → turns=1 < prev_max=2 → rewound=True
    truncated = home / "truncated.jsonl"
    truncated.write_text("", encoding="utf-8")

    result = _run_hook(
        home,
        {
            "session_id": sid,
            "transcript_path": str(truncated),
            "cwd": str(tmp_path),
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "after rewind prompt",
        },
        rollover_turns=2,
    )

    # Seed must NOT have been overwritten
    assert seed_path.exists(), "seed.md must still exist after rewind"
    assert seed_path.read_text(encoding="utf-8") == seed_content, "seed.md must not be overwritten on rewind"

    # additionalContext must contain the durable seed block
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "[BURNLESS ROLLING MEMORY" in ctx, "additionalContext must contain the durable seed header"


def test_rotation_due_flag_written(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    sid = "sess-rotdue"

    transcript = home / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "first message"}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "first reply"}}) + "\n",
        encoding="utf-8",
    )

    # Activate rollover mode
    _run_hook(
        home,
        {
            "session_id": sid,
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "/burnless rollover",
        },
        rollover_turns=2,
    )

    # Run at rotation point: transcript has 1 user turn, prompt adds 2nd → turns=2 → rotation_due written
    _run_hook(
        home,
        {
            "session_id": sid,
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "second user turn",
        },
        rollover_turns=2,
    )

    state_dir = home / ".burnless" / "state"
    flag_path = state_dir / "rotation_due"
    assert flag_path.exists(), "rotation_due must exist after rotation point"
    assert flag_path.read_text(encoding="utf-8").strip(), "rotation_due must not be empty"

    # Simulate /rewind: delete flag, then run with empty transcript → rewound=True → flag must NOT reappear
    flag_path.unlink()
    truncated = home / "truncated.jsonl"
    truncated.write_text("", encoding="utf-8")

    _run_hook(
        home,
        {
            "session_id": sid,
            "transcript_path": str(truncated),
            "cwd": str(tmp_path),
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "after rewind prompt",
        },
        rollover_turns=2,
    )

    assert not flag_path.exists(), "rotation_due must NOT be written on rewind"
