"""Tests for owner-loop telemetry logging."""

import json
import tempfile
from pathlib import Path

from burnless.owner_loop import log_owner_event


def test_log_owner_event_appends_jsonl():
    """Verify log_owner_event appends 2 events as separate JSON lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        log_owner_event(root, {"event": "test1", "phase": "carry_forward"})
        log_owner_event(root, {"event": "test2", "phase": "refine"})

        log_file = root / ".burnless" / "owner_loop.jsonl"
        assert log_file.exists(), f"Log file not created at {log_file}"

        lines = log_file.read_text(encoding='utf-8').strip().split('\n')
        assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}"

        obj1 = json.loads(lines[0])
        obj2 = json.loads(lines[1])

        assert obj1 == {"event": "test1", "phase": "carry_forward"}
        assert obj2 == {"event": "test2", "phase": "refine"}


def test_log_owner_event_never_raises():
    """Verify log_owner_event never raises exception on invalid inputs."""
    # None root
    log_owner_event(None, {"event": "test"})

    # Invalid path
    log_owner_event("/invalid/path/that/does/not/exist/xyz", {"event": "test"})

    # Path-like invalid
    log_owner_event(Path("/impossible/burnless/path"), {"event": "test"})

    # No exception should be raised for any of these
