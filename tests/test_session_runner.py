"""Offline unit tests for MaestroSession — assert command construction per state.
No real subprocess or LLM calls; runner is injected as a mock callable.
"""
from __future__ import annotations

import pytest
from burnless.maestro.session_runner import MaestroSession, MAESTRO_DISALLOWED


def mock_runner(session_id: str, output_tokens: int = 5, result_text: str = "ok"):
    """Return a runner callable that always succeeds with the given session_id."""
    def _run(cmd: list[str]) -> dict:
        return {"session_id": session_id, "usage": {"output_tokens": output_tokens}, "result": result_text}
    return _run


def has_flag(cmd: list[str], flag: str) -> bool:
    return flag in cmd


def flag_value(cmd: list[str], flag: str) -> str | None:
    try:
        i = cmd.index(flag)
        return cmd[i + 1]
    except (ValueError, IndexError):
        return None


# --- cycle-start ---

def test_cycle_start_forks_base():
    s = MaestroSession(base_uuid="BASE-UUID", model="claude-haiku-4-5-20251001")
    cmd = s.build_command("hello")
    assert "--resume" in cmd
    assert "BASE-UUID" in cmd
    assert "--fork-session" in cmd


def test_cycle_start_resume_value_is_base():
    s = MaestroSession(base_uuid="BASE-UUID", model="claude-haiku-4-5-20251001")
    cmd = s.build_command("hello")
    assert flag_value(cmd, "--resume") == "BASE-UUID"


# --- mid-cycle ---

def test_mid_cycle_resumes_fork_not_base():
    s = MaestroSession(base_uuid="BASE-UUID", model="claude-haiku-4-5-20251001")
    s.send("first", runner=mock_runner("fork1"))
    cmd = s.build_command("second")
    assert flag_value(cmd, "--resume") == "fork1"


def test_mid_cycle_no_fork_session_flag():
    s = MaestroSession(base_uuid="BASE-UUID", model="claude-haiku-4-5-20251001")
    s.send("first", runner=mock_runner("fork1"))
    cmd = s.build_command("second")
    assert "--fork-session" not in cmd


# --- rewind ---

def test_rewind_restores_fork_base():
    s = MaestroSession(base_uuid="BASE-UUID", model="claude-haiku-4-5-20251001")
    s.send("first", runner=mock_runner("fork1"))
    s.rewind()
    cmd = s.build_command("new cycle")
    assert "--fork-session" in cmd
    assert flag_value(cmd, "--resume") == "BASE-UUID"


def test_rewind_then_send_sets_new_fork():
    s = MaestroSession(base_uuid="BASE-UUID", model="claude-haiku-4-5-20251001")
    s.send("first", runner=mock_runner("fork1"))
    s.rewind()
    s.send("second", runner=mock_runner("fork2"))
    cmd = s.build_command("third")
    assert flag_value(cmd, "--resume") == "fork2"
    assert "--fork-session" not in cmd


# --- tool-less by policy ---

def test_every_command_has_disallowed_tools_cycle_start():
    s = MaestroSession(base_uuid="B", model="m")
    cmd = s.build_command("hi")
    assert "--disallowedTools" in cmd


def test_every_command_has_disallowed_tools_mid_cycle():
    s = MaestroSession(base_uuid="B", model="m")
    s.send("first", runner=mock_runner("f1"))
    cmd = s.build_command("second")
    assert "--disallowedTools" in cmd


def test_disallowed_tools_value_contains_edit():
    s = MaestroSession(base_uuid="B", model="m")
    cmd = s.build_command("hi")
    val = flag_value(cmd, "--disallowedTools")
    assert val is not None
    assert "Edit" in val


def test_never_tools_empty_string_cycle_start():
    s = MaestroSession(base_uuid="B", model="m")
    cmd = s.build_command("hi")
    for i in range(len(cmd) - 1):
        assert not (cmd[i] == "--tools" and cmd[i + 1] == ""), f"Found --tools '' at index {i}: {cmd}"


def test_never_tools_empty_string_mid_cycle():
    s = MaestroSession(base_uuid="B", model="m")
    s.send("first", runner=mock_runner("f1"))
    cmd = s.build_command("second")
    for i in range(len(cmd) - 1):
        assert not (cmd[i] == "--tools" and cmd[i + 1] == ""), f"Found --tools '' at index {i}: {cmd}"


# --- rewind_capsule ---

def test_rewind_capsule_prefixes_message():
    s = MaestroSession(base_uuid="B", model="m")
    cmd = s.build_command("the task", rewind_capsule="PRIOR STATE")
    # The user message is the -p argument value
    msg = flag_value(cmd, "-p")
    assert msg is not None
    assert "PRIOR STATE" in msg
    assert "the task" in msg


def test_rewind_capsule_header_present():
    s = MaestroSession(base_uuid="B", model="m")
    cmd = s.build_command("task", rewind_capsule="S")
    msg = flag_value(cmd, "-p")
    assert "## State (carry-forward)" in msg


def test_no_rewind_capsule_plain_message():
    s = MaestroSession(base_uuid="B", model="m")
    cmd = s.build_command("plain message")
    msg = flag_value(cmd, "-p")
    assert msg == "plain message"


# --- send() tracks usage and returns (text, tokens) ---

def test_send_returns_text_and_tokens():
    s = MaestroSession(base_uuid="B", model="m")
    text, tokens = s.send("hi", runner=mock_runner("f1", output_tokens=42, result_text="reply"))
    assert text == "reply"
    assert tokens == 42


def test_send_records_usage():
    s = MaestroSession(base_uuid="B", model="m")
    s.send("first", runner=mock_runner("f1", output_tokens=10))
    s.send("second", runner=mock_runner("f2", output_tokens=20))
    assert len(s.usages) == 2
    assert s.usages[0].get("output_tokens") == 10
    assert s.usages[1].get("output_tokens") == 20


def test_send_zero_tokens_on_missing_usage():
    def runner_no_usage(cmd):
        return {"session_id": "f1", "result": "ok"}
    s = MaestroSession(base_uuid="B", model="m")
    text, tokens = s.send("hi", runner=runner_no_usage)
    assert tokens == 0


def test_send_preserves_fork_id_if_result_missing_session_id():
    s = MaestroSession(base_uuid="B", model="m")
    # First send sets fork1
    s.send("first", runner=mock_runner("fork1"))
    # Second send returns no session_id
    def runner_no_sid(cmd):
        return {"usage": {"output_tokens": 3}, "result": "ok"}
    s.send("second", runner=runner_no_sid)
    # fork_session_id should still be fork1
    assert s.fork_session_id == "fork1"


# --- MAESTRO_DISALLOWED constant sanity ---

def test_maestro_disallowed_constant():
    # Must include the key dangerous tools
    for tool in ("Edit", "Write", "Bash", "Read"):
        assert tool in MAESTRO_DISALLOWED
