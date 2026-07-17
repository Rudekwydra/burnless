from __future__ import annotations

from datetime import datetime, timedelta, timezone

from burnless.pilot import append_event
from burnless.pilot.events import summarize_run_events
from burnless.pilot.rollover import SessionIdentity, resolve_session_identity


def test_resolve_returns_real_sid(tmp_path):
    root = tmp_path / ".burnless"
    t0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
    append_event(
        root,
        "pilot-r1",
        {
            "ts": (t0 + timedelta(seconds=0.5)).isoformat(),
            "event": "turn_start",
            "host": "claude",
            "host_session_id": "real-child-xyz",
            "process_instance_id": "host-42",
        },
    )
    append_event(
        root,
        "pilot-r1",
        {
            "ts": (t0 + timedelta(seconds=1)).isoformat(),
            "event": "stop",
            "host": "claude",
            "host_session_id": "real-child-xyz",
            "process_instance_id": "host-42",
        },
    )

    ident = resolve_session_identity(root, "pilot-r1")

    assert ident is not None
    assert isinstance(ident, SessionIdentity)
    assert ident.host_session_id == "real-child-xyz"
    assert ident.process_instance_id == "host-42"
    assert ident.run_id == "pilot-r1"


def test_resolve_none_without_real_sid(tmp_path):
    root = tmp_path / ".burnless"
    t0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
    append_event(
        root,
        "pilot-r2",
        {
            "ts": (t0 + timedelta(seconds=0.5)).isoformat(),
            "event": "turn_start",
            "host": "claude",
        },
    )
    append_event(
        root,
        "pilot-r2",
        {
            "ts": (t0 + timedelta(seconds=1)).isoformat(),
            "event": "stop",
            "host": "claude",
        },
    )

    ident = resolve_session_identity(root, "pilot-r2")

    assert ident is None


def test_resolve_scans_back_to_last_identified(tmp_path):
    root = tmp_path / ".burnless"
    t0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
    append_event(
        root,
        "pilot-r3",
        {
            "ts": (t0 + timedelta(seconds=0.5)).isoformat(),
            "event": "turn_start",
            "host": "claude",
            "host_session_id": "sid-A",
            "process_instance_id": "host-1",
        },
    )
    append_event(
        root,
        "pilot-r3",
        {
            "ts": (t0 + timedelta(seconds=1)).isoformat(),
            "event": "clear",
            "host": "claude",
        },
    )

    ident = resolve_session_identity(root, "pilot-r3")

    assert ident is not None
    assert ident.host_session_id == "sid-A"


def test_summarize_exposes_last_identity(tmp_path):
    root = tmp_path / ".burnless"
    t0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
    append_event(
        root,
        "pilot-r4",
        {
            "ts": (t0 + timedelta(seconds=0.5)).isoformat(),
            "event": "turn_start",
            "host": "claude",
            "host_session_id": "sid-Z",
            "process_instance_id": "host-7",
        },
    )

    state = summarize_run_events(root, "pilot-r4")

    assert state["last_identity"]["host_session_id"] == "sid-Z"
