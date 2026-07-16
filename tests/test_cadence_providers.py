from __future__ import annotations

import json
from pathlib import Path

from burnless.pilot.cadence_providers import (
    resolve_transcript_path,
    backlog_turns_since_last_compact,
    epoch_focus,
    build_cadence_controller,
)
from burnless.pilot.core import ContextUsage, HostSession


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def test_backlog_counts_all_assistant_when_no_compact(tmp_path):
    tp = tmp_path / "t.jsonl"
    _write_jsonl(tp, [
        {"type": "user"},
        {"type": "assistant", "message": {}},
        {"type": "assistant", "message": {}},
        {"type": "user"},
    ])
    assert backlog_turns_since_last_compact(tp) == 2


def test_backlog_counts_only_after_last_compact(tmp_path):
    tp = tmp_path / "t.jsonl"
    _write_jsonl(tp, [
        {"type": "assistant", "message": {}},
        {"type": "user", "isCompactSummary": True, "sessionId": "s", "message": {"content": "sum"}},
        {"type": "assistant", "message": {}},
        {"type": "assistant", "message": {}},
        {"type": "assistant", "message": {}},
    ])
    assert backlog_turns_since_last_compact(tp) == 3


def test_backlog_missing_file_is_zero(tmp_path):
    assert backlog_turns_since_last_compact(tmp_path / "nope.jsonl") == 0
    assert backlog_turns_since_last_compact(None) == 0


def test_resolve_transcript_path_reads_events_ref(tmp_path):
    root = tmp_path
    runs = root / ".burnless" / "pilot" / "runs" / "r1"
    runs.mkdir(parents=True, exist_ok=True)
    transcript = root / "real.jsonl"
    transcript.write_text("", encoding="utf-8")
    from burnless.pilot.events import events_path
    ep = events_path(root, "r1")
    ep.parent.mkdir(parents=True, exist_ok=True)
    with ep.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"transcript_ref": str(transcript)}) + "\n")
    assert resolve_transcript_path(root, "r1") == transcript


def test_epoch_focus_is_empty_v1(tmp_path):
    assert epoch_focus(tmp_path, "chat") == ""


class _FakeAdapter:
    name = "claude"

    def locate_session(self, run_id):
        return HostSession(host="claude", host_session_id=run_id, process_instance_id=run_id, cwd=None)

    def context_usage(self, session):
        return ContextUsage(current=100, limit=200)

    def is_turn_idle(self, session):
        return True


def test_build_cadence_controller_providers_work(tmp_path):
    ctrl = build_cadence_controller(
        adapter=_FakeAdapter(),
        project_root=tmp_path,
        run_id="r1",
        host_session_id="r1",
        cfg={"poll_interval_s": 0.0, "cooldown_s": 0.0},
    )
    assert ctrl.usage_provider().current == 100
    assert ctrl.idle_provider() is True
    assert ctrl.backlog_provider() == 0
    assert ctrl.focus_provider() == ""


def test_resolve_transcript_path_falls_back_to_project_dir(tmp_path, monkeypatch):
    import burnless.usage_meter as um
    pdir = tmp_path / "proj"
    pdir.mkdir()
    older = pdir / "old.jsonl"
    older.write_text("", encoding="utf-8")
    newer = pdir / "new.jsonl"
    newer.write_text("", encoding="utf-8")
    import os as _os
    _os.utime(older, (1, 1))
    _os.utime(newer, (100, 100))
    monkeypatch.setattr(um, "claude_project_dir", lambda *, cwd=None: pdir)
    # events file absent -> must fall back to newest jsonl
    assert resolve_transcript_path(tmp_path / "noevents", "rX") == newer
