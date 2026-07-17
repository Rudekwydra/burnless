from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from burnless.pilot import append_event
from burnless.pilot.rollover import prepare_rollover

REAL_SID = "real-child-abc"
PLACEHOLDER = "pilot-run-1"
RUN = "pilot-run-1"


def _write_checkpoint(root, sid: str) -> None:
    ckpt_dir = root / "epochs" / "sessions" / "claude" / sid
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generation": 1,
        "living_md": "MARKER_WORK_CONTEXT",
        "harvested_state": {"contracts": [], "refs": [], "open_threads": []},
        "applied_through": 0,
        "journal_head": 0,
        "host_session_id": sid,
    }
    with (ckpt_dir / "checkpoint.json").open("w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_prepare_rollover_uses_real_sid_from_events(tmp_path):
    root = tmp_path / ".burnless"
    _write_checkpoint(root, REAL_SID)

    t0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
    append_event(
        root,
        RUN,
        {
            "ts": (t0 + timedelta(seconds=0.5)).isoformat(),
            "event": "turn_start",
            "host": "claude",
            "host_session_id": REAL_SID,
            "process_instance_id": "host-999",
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
            "process_instance_id": "host-999",
        },
    )

    result = prepare_rollover(
        root,
        host="claude",
        host_session_id=PLACEHOLDER,
        process_instance_id=PLACEHOLDER,
        run_id=RUN,
        new_session_id="fresh-1",
        since_ts=t0.isoformat(),
    )

    assert result["status"] == "ready"
    assert result["handoff"]["host_session_id"] == REAL_SID
    assert "MARKER_WORK_CONTEXT" in json.dumps(result["restore"])


def test_prepare_rollover_placeholder_when_no_real_sid(tmp_path):
    root = tmp_path / ".burnless"
    _write_checkpoint(root, REAL_SID)

    t0 = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
    append_event(
        root,
        RUN,
        {
            "ts": (t0 + timedelta(seconds=0.5)).isoformat(),
            "event": "turn_start",
            "host": "claude",
        },
    )
    append_event(
        root,
        RUN,
        {
            "ts": (t0 + timedelta(seconds=1)).isoformat(),
            "event": "stop",
            "host": "claude",
        },
    )

    result = prepare_rollover(
        root,
        host="claude",
        host_session_id=PLACEHOLDER,
        process_instance_id=PLACEHOLDER,
        run_id=RUN,
        new_session_id="fresh-1",
        since_ts=t0.isoformat(),
    )

    assert result["status"] == "not_ready"
    assert not result["restore"]
