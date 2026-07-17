from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_stale_stop_filtered_by_since_ts(tmp_path):
    from burnless.pilot import append_event, summarize_run_events

    root = tmp_path / ".burnless"
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    since = t0 + timedelta(seconds=1)

    append_event(
        root,
        "run-1",
        {
            "event": "stop",
            "host": "claude",
            "host_session_id": "old-sid",
            "process_instance_id": "proc-1",
            "ts": _iso(t0),
        },
    )

    summary = summarize_run_events(root, "run-1", since_ts=_iso(since))
    assert summary["idle"] is False
    assert summary["count"] == 0


def test_should_rollover_stale_idle_not_armed(tmp_path):
    from burnless.pilot import append_event
    from burnless.pilot.core import ContextUsage
    from burnless.pilot.rollover import should_rollover

    root = tmp_path / ".burnless"
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    since = t0 + timedelta(seconds=1)

    append_event(
        root,
        "run-1",
        {
            "event": "stop",
            "host": "claude",
            "host_session_id": "old-sid",
            "process_instance_id": "proc-1",
            "ts": _iso(t0),
        },
    )

    decision = should_rollover(
        root,
        host="claude",
        host_session_id="old-sid",
        process_instance_id="proc-1",
        run_id="run-1",
        context_usage=ContextUsage(current=152333, limit=200000, confidence="estimated"),
        rollover_at_tokens=40000,
        rollover_at_pct=0.65,
        trusted_confidences=("exact", "estimated"),
        since_ts=_iso(since),
    )
    assert decision["should_rollover"] is False
    assert decision["reason"] == "run_not_idle"


def test_should_rollover_fresh_stop_arms(tmp_path):
    from burnless.pilot import append_event
    from burnless.pilot.core import ContextUsage
    from burnless.pilot.rollover import should_rollover

    root = tmp_path / ".burnless"
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    since = t0
    fresh_stop = t0 + timedelta(seconds=1)

    append_event(
        root,
        "run-1",
        {
            "event": "stop",
            "host": "claude",
            "host_session_id": "new-sid",
            "process_instance_id": "proc-1",
            "ts": _iso(fresh_stop),
        },
    )

    decision = should_rollover(
        root,
        host="claude",
        host_session_id="new-sid",
        process_instance_id="proc-1",
        run_id="run-1",
        context_usage=ContextUsage(current=152333, limit=200000, confidence="estimated"),
        rollover_at_tokens=40000,
        rollover_at_pct=0.65,
        trusted_confidences=("exact", "estimated"),
        since_ts=_iso(since),
    )
    assert decision["should_rollover"] is True


def test_circuit_open_true_when_rapid():
    from burnless.cli import _pilot_rollover_circuit_open

    t = 1_000_000.0
    assert _pilot_rollover_circuit_open([t, t + 1, t + 2], t + 2) is True
    assert _pilot_rollover_circuit_open([t, t + 60, t + 120], t + 120) is False
