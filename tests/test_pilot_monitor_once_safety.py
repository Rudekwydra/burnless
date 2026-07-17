from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from burnless.pilot import append_event
from burnless.pilot.core import ContextUsage
from burnless.pilot.rollover import monitor_rollover_once

REAL_SID = "real-sid-x"
RUN = "pilot-run"


def _write_checkpoint(root, sid: str) -> None:
    ckpt_dir = root / "epochs" / "sessions" / "claude" / sid
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generation": 1,
        "living_md": "CTX",
        "harvested_state": {"contracts": [], "refs": [], "open_threads": []},
        "applied_through": 0,
        "journal_head": 0,
        "host_session_id": sid,
    }
    with (ckpt_dir / "checkpoint.json").open("w", encoding="utf-8") as f:
        json.dump(payload, f)


def _write_events(root, t0):
    append_event(
        root,
        RUN,
        {
            "ts": (t0 + timedelta(seconds=0.5)).isoformat(),
            "event": "turn_start",
            "host": "claude",
            "host_session_id": REAL_SID,
            "process_instance_id": "host-1",
        },
    )
    append_event(
        root,
        RUN,
        {
            "ts": (t0 + timedelta(seconds=1)).isoformat(),
            "event": "stop",
            "host": "claude",
            "host_session_id": REAL_SID,
            "process_instance_id": "host-1",
        },
    )


def test_no_kill_when_prepare_not_ready(tmp_path):
    root = tmp_path / ".burnless"
    t0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
    _write_events(root, t0)

    result = monitor_rollover_once(
        root,
        host="claude",
        host_session_id="pilot-run",
        process_instance_id="pilot-run",
        run_id="pilot-run",
        new_session_id="fresh",
        context_usage=ContextUsage(current=152333, limit=200000, confidence="estimated"),
        rollover_at_tokens=40000,
        trusted_confidences=("exact", "estimated"),
        since_ts=t0.isoformat(),
    )

    assert result["status"] != "prepared"
    assert result["status"] == "not_ready"


def test_kill_only_when_prepare_ready(tmp_path):
    root = tmp_path / ".burnless"
    _write_checkpoint(root, REAL_SID)
    t0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
    _write_events(root, t0)

    result = monitor_rollover_once(
        root,
        host="claude",
        host_session_id="pilot-run",
        process_instance_id="pilot-run",
        run_id="pilot-run",
        new_session_id="fresh",
        context_usage=ContextUsage(current=152333, limit=200000, confidence="estimated"),
        rollover_at_tokens=40000,
        trusted_confidences=("exact", "estimated"),
        since_ts=t0.isoformat(),
    )

    assert result["status"] == "prepared"
