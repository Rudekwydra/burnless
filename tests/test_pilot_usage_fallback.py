"""Tests for pilot usage fallback and confidence gating logic."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from burnless.pilot.core import ContextUsage, PilotEvent
from burnless.pilot.logs import claude_context_usage, _last_assistant_usage_tokens
from burnless.pilot.rollover import should_rollover
from burnless.pilot import events as events_mod


def test_fallback_unknown_when_no_transcripts(tmp_path, monkeypatch):
    """When no transcripts exist, fallback returns confidence='unknown'."""
    empty_project_dir = tmp_path / "empty_project"
    empty_project_dir.mkdir()

    def mock_claude_project_dir(**kwargs):
        return empty_project_dir

    monkeypatch.setattr("burnless.pilot.logs.claude_project_dir", mock_claude_project_dir)

    result = claude_context_usage(cwd=str(tmp_path))
    assert result.confidence == "unknown"
    assert result.current is None


def test_fallback_last_assistant_usage(tmp_path, monkeypatch):
    """Fallback extracts usage from the last assistant record in newest transcript."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    transcript = project_dir / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"text": "hi"}}) + "\n"
        + json.dumps({
            "type": "assistant",
            "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 5}}
        }) + "\n"
        + json.dumps({
            "type": "assistant",
            "message": {"usage": {"input_tokens": 2, "cache_read_input_tokens": 30000, "cache_creation_input_tokens": 1000}}
        }) + "\n",
        encoding="utf-8"
    )

    def mock_claude_project_dir(**kwargs):
        return project_dir

    monkeypatch.setattr("burnless.pilot.logs.claude_project_dir", mock_claude_project_dir)

    result = claude_context_usage(cwd=str(tmp_path))
    assert result.current == 31002  # 2 + 30000 + 1000
    assert result.confidence == "estimated"


def test_rollover_estimated_untrusted(tmp_path):
    """Rollover blocks when usage confidence='estimated' and not trusted."""
    root = tmp_path / "burnless_root"
    root.mkdir()

    run_id = "test_run"
    events_mod.append_event(
        root,
        run_id,
        PilotEvent(
            host="claude",
            host_session_id="s1",
            process_instance_id="p1",
            event="stop"
        )
    )

    result = should_rollover(
        root,
        host="claude",
        host_session_id="s1",
        process_instance_id="p1",
        run_id=run_id,
        context_usage=ContextUsage(current=9999999, limit=200000, confidence="estimated"),
        rollover_at_tokens=40000,
        trusted_confidences=("exact",)
    )

    assert result["should_rollover"] is False
    assert result["reason"] == "usage_estimated_untrusted"


def test_rollover_estimated_trusted_when_opted_in(tmp_path):
    """Rollover triggers when usage confidence='estimated' and trusted."""
    root = tmp_path / "burnless_root"
    root.mkdir()

    run_id = "test_run"
    events_mod.append_event(
        root,
        run_id,
        PilotEvent(
            host="claude",
            host_session_id="s1",
            process_instance_id="p1",
            event="stop"
        )
    )

    result = should_rollover(
        root,
        host="claude",
        host_session_id="s1",
        process_instance_id="p1",
        run_id=run_id,
        context_usage=ContextUsage(current=9999999, limit=200000, confidence="estimated"),
        rollover_at_tokens=40000,
        trusted_confidences=("exact", "estimated")
    )

    assert result["should_rollover"] is True


def test_rollover_exact_still_triggers(tmp_path):
    """Rollover triggers when usage confidence='exact' with default trusted_confidences."""
    root = tmp_path / "burnless_root"
    root.mkdir()

    run_id = "test_run"
    events_mod.append_event(
        root,
        run_id,
        PilotEvent(
            host="claude",
            host_session_id="s1",
            process_instance_id="p1",
            event="stop"
        )
    )

    result = should_rollover(
        root,
        host="claude",
        host_session_id="s1",
        process_instance_id="p1",
        run_id=run_id,
        context_usage=ContextUsage(current=9999999, limit=200000, confidence="exact"),
        rollover_at_tokens=40000,
    )

    assert result["should_rollover"] is True
