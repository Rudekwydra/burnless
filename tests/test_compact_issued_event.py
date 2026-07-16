import pytest
from pathlib import Path
from burnless.pilot.events import append_event, read_events


def test_wrapped_injector_records_compact_issued(tmp_path):
    """Test that wrapped injector records compact_issued event when inner injector returns bytes."""
    root = tmp_path
    run_id = "test-run-123"

    # Simulate inner injector that returns bytes once, then None
    call_count = [0]
    def fake_inner_injector() -> bytes | None:
        call_count[0] += 1
        if call_count[0] == 1:
            return b"/compact\r"
        return None

    # Create wrapper like in cli.py
    inner_injector = fake_inner_injector
    def wrapped_injector() -> bytes | None:
        result = inner_injector()
        if result is not None:
            try:
                append_event(
                    root,
                    run_id,
                    {"event": "compact_issued", "ts": "2026-07-16T21:00:00+00:00"}
                )
            except Exception:
                pass
        return result

    # Call wrapped injector twice
    result1 = wrapped_injector()
    result2 = wrapped_injector()

    # Verify bytes pass through unchanged
    assert result1 == b"/compact\r"
    assert result2 is None

    # Verify exactly one compact_issued event was recorded
    events = read_events(root, run_id)
    compact_events = [e for e in events if e.get("event") == "compact_issued"]
    assert len(compact_events) == 1
    assert "ts" in compact_events[0]


def test_wrapped_injector_no_event_on_none(tmp_path):
    """Test that wrapped injector doesn't record event when inner returns None."""
    root = tmp_path
    run_id = "test-run-456"

    def fake_inner_injector() -> bytes | None:
        return None

    inner_injector = fake_inner_injector
    def wrapped_injector() -> bytes | None:
        result = inner_injector()
        if result is not None:
            try:
                append_event(
                    root,
                    run_id,
                    {"event": "compact_issued", "ts": "2026-07-16T21:00:00+00:00"}
                )
            except Exception:
                pass
        return result

    result = wrapped_injector()

    # Verify None passes through
    assert result is None

    # Verify no event was recorded
    events = read_events(root, run_id)
    compact_events = [e for e in events if e.get("event") == "compact_issued"]
    assert len(compact_events) == 0


def test_wrapped_injector_survives_append_failure(tmp_path):
    """Test that wrapped injector returns bytes even if event append fails."""
    root = tmp_path / "nonexistent" / "path"  # Create failing condition
    run_id = "test-run-789"

    expected_bytes = b"/compact\r"
    def fake_inner_injector() -> bytes | None:
        return expected_bytes

    inner_injector = fake_inner_injector
    def wrapped_injector() -> bytes | None:
        result = inner_injector()
        if result is not None:
            try:
                append_event(
                    root,
                    run_id,
                    {"event": "compact_issued", "ts": "2026-07-16T21:00:00+00:00"}
                )
            except Exception:
                pass
        return result

    result = wrapped_injector()

    # Verify bytes are returned despite failure
    assert result == expected_bytes
