"""Test _continue_after_interrupt stream-json translation and session_id capture."""

import json
import queue
import tempfile
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from burnless.live_runner import _continue_after_interrupt, _PanelEventFilter


class FakeProc:
    """Fake subprocess.Popen for testing."""
    def __init__(self, returncode=0):
        self.returncode = returncode
    def poll(self):
        return self.returncode


def make_stream_json_event(text: str = "", session_id: str | None = None) -> str:
    """Build a claude stream-json assistant event with optional final session_id."""
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": text}
            ] if text else [],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
        }
    }
    return json.dumps(event, ensure_ascii=False)


def make_result_event(session_id: str | None = None) -> str:
    """Build a claude stream-json result event with session_id."""
    event = {
        "type": "result",
        "result": "",
        "session_id": session_id,
        "is_error": False,
    }
    return json.dumps(event, ensure_ascii=False)


@pytest.fixture
def test_env(tmp_path):
    """Provide fixture with temp log file and common kwargs."""
    log_file = tmp_path / "test.log"

    return {
        "tmp_path": tmp_path,
        "log_file": log_file,
        "proc": FakeProc(returncode=0),
        "events": queue.Queue(),
        "mode": "plain",
        "event_filter": _PanelEventFilter(),
        "recent": deque(maxlen=20),
        "delegation_id": "d000",
        "tier": "silver",
        "agent_cfg": {"name": "test_agent"},
        "log_path": Path(tmp_path) / "test.log",
        "burnless_tokens": 0,
        "refresh_rate": 1.0,
        "phase_sink": None,
        "start_mono": time.monotonic(),
        "started": datetime.now(timezone.utc),
        "command": ["test_cmd"],
    }


def test_stdout_consolidado_nao_ndjson(test_env):
    """Verify that stream-json is translated and stdout contains consolidated text, not raw envelope."""
    log_file = test_env["log_file"]

    # Pre-load events queue with stream-json events
    test_text = "Hello from Claude"
    test_env["events"].put(("stdout", make_stream_json_event(text=test_text) + "\n"))
    test_env["events"].put(("stdout", make_result_event(session_id="sid-12345") + "\n"))

    # Run _continue_after_interrupt
    with open(log_file, "w") as log:
        result = _continue_after_interrupt(
            proc=test_env["proc"],
            events=test_env["events"],
            log=log,
            stdout_parts=[],
            stderr_parts=[],
            recent=test_env["recent"],
            mode=test_env["mode"],
            event_filter=test_env["event_filter"],
            start_mono=test_env["start_mono"],
            started=test_env["started"],
            command=test_env["command"],
            agent_cfg=test_env["agent_cfg"],
            delegation_id=test_env["delegation_id"],
            tier=test_env["tier"],
            log_path=test_env["log_path"],
            burnless_tokens=test_env["burnless_tokens"],
            refresh_rate=test_env["refresh_rate"],
            phase_sink=test_env["phase_sink"],
            consolidated_text=[],
            session_holder=[],
            saw_stream_json=False,
        )

    # Verify: stdout contains consolidated text, NOT raw envelope with "type"
    assert test_text in result.stdout, f"Expected '{test_text}' in stdout, got: {result.stdout}"
    assert '"type"' not in result.stdout, f"Stdout should not contain raw JSON envelope, got: {result.stdout}"
    assert "[text]" in result.stdout or test_text in result.stdout, "stdout should contain translated text"


def test_session_id_extraido(test_env):
    """Verify that session_id from result event is captured."""
    log_file = test_env["log_file"]
    test_session_id = "sess-abc123xyz"

    # Pre-load events with result event containing session_id
    test_env["events"].put(("stdout", make_stream_json_event(text="Final result") + "\n"))
    test_env["events"].put(("stdout", make_result_event(session_id=test_session_id) + "\n"))

    with open(log_file, "w") as log:
        result = _continue_after_interrupt(
            proc=test_env["proc"],
            events=test_env["events"],
            log=log,
            stdout_parts=[],
            stderr_parts=[],
            recent=test_env["recent"],
            mode=test_env["mode"],
            event_filter=test_env["event_filter"],
            start_mono=test_env["start_mono"],
            started=test_env["started"],
            command=test_env["command"],
            agent_cfg=test_env["agent_cfg"],
            delegation_id=test_env["delegation_id"],
            tier=test_env["tier"],
            log_path=test_env["log_path"],
            burnless_tokens=test_env["burnless_tokens"],
            refresh_rate=test_env["refresh_rate"],
            phase_sink=test_env["phase_sink"],
            consolidated_text=[],
            session_holder=[],
            saw_stream_json=False,
        )

    # Verify: session_id is extracted and set
    assert result.session_id == test_session_id, f"Expected session_id '{test_session_id}', got: {result.session_id}"


def test_fallback_texto_puro(test_env):
    """Verify fallback to raw text when events are NOT JSON (legacy text mode)."""
    log_file = test_env["log_file"]
    plain_text_line = "Some plain text output from worker\n"

    # Pre-load events with plain text (not JSON)
    test_env["events"].put(("stdout", plain_text_line))

    with open(log_file, "w") as log:
        result = _continue_after_interrupt(
            proc=test_env["proc"],
            events=test_env["events"],
            log=log,
            stdout_parts=[],
            stderr_parts=[],
            recent=test_env["recent"],
            mode=test_env["mode"],
            event_filter=test_env["event_filter"],
            start_mono=test_env["start_mono"],
            started=test_env["started"],
            command=test_env["command"],
            agent_cfg=test_env["agent_cfg"],
            delegation_id=test_env["delegation_id"],
            tier=test_env["tier"],
            log_path=test_env["log_path"],
            burnless_tokens=test_env["burnless_tokens"],
            refresh_rate=test_env["refresh_rate"],
            phase_sink=test_env["phase_sink"],
            consolidated_text=[],
            session_holder=[],
            saw_stream_json=False,
        )

    # Verify: stdout is plain concatenation and session_id is None
    assert plain_text_line.strip() in result.stdout, f"Expected plain text in stdout, got: {result.stdout}"
    assert result.session_id is None, f"Expected session_id None for plain text, got: {result.session_id}"
