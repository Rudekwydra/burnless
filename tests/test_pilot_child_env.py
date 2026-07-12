import pytest
from burnless.pilot.core import build_child_env


def test_scrubs_claude_session_identity_vars(monkeypatch):
    """Verify that build_child_env removes all Claude session-identity variables."""
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_BRIDGE_SESSION_ID", "x")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")

    result = build_child_env("r1")

    assert "CLAUDECODE" not in result
    assert "CLAUDE_CODE_SESSION_ID" not in result
    assert "CLAUDE_CODE_CHILD_SESSION" not in result
    assert "CLAUDE_CODE_BRIDGE_SESSION_ID" not in result
    assert "CLAUDE_CODE_ENTRYPOINT" not in result


def test_keeps_everything_else_and_sets_run_id(monkeypatch):
    """Verify that build_child_env preserves non-Claude vars and sets BURNLESS_PILOT_RUN_ID."""
    monkeypatch.setenv("HOME", "/tmp/h")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("BURNLESS_HARDCORE", "1")

    result = build_child_env("r1")

    assert result["HOME"] == "/tmp/h"
    assert result["PATH"] == "/usr/bin"
    assert result["BURNLESS_HARDCORE"] == "1"
    assert result["BURNLESS_PILOT_RUN_ID"] == "r1"


def test_no_claude_vars_is_noop_plus_run_id(monkeypatch):
    """Verify that build_child_env with no Claude vars returns env + BURNLESS_PILOT_RUN_ID."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    # Clear all CLAUDE_CODE_* vars that might exist
    for key in list(pytest.importorskip("os").environ.keys()):
        if key.startswith("CLAUDE_CODE_"):
            monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("TEST_VAR", "preserved")
    result = build_child_env("r1")

    assert result["TEST_VAR"] == "preserved"
    assert result["BURNLESS_PILOT_RUN_ID"] == "r1"
