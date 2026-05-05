"""Smoke tests for P1: keepalive schema fields + touch_activity helper."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from burnless.state import DEFAULT_STATE, load, save, touch_activity


def test_default_state_has_keepalive_fields():
    assert "last_activity_ts" in DEFAULT_STATE
    assert "next_keepalive_ts" in DEFAULT_STATE
    assert "keepalive_last_ts" in DEFAULT_STATE
    assert "keepalive_last_status" in DEFAULT_STATE
    assert DEFAULT_STATE["last_activity_ts"] is None
    assert DEFAULT_STATE["next_keepalive_ts"] is None


def test_touch_activity_sets_fields():
    state: dict = {}
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    touch_activity(state, idle_threshold_s=3000, now=now)
    assert state["last_activity_ts"] == now.isoformat()
    expected_next = (now + timedelta(seconds=3000)).isoformat()
    assert state["next_keepalive_ts"] == expected_next


def test_touch_activity_default_now():
    state: dict = {}
    before = datetime.now(timezone.utc)
    touch_activity(state)
    after = datetime.now(timezone.utc)
    ts = datetime.fromisoformat(state["last_activity_ts"])
    assert before <= ts <= after


def test_touch_activity_does_not_call_save(tmp_path):
    state_path = tmp_path / "state.json"
    state: dict = {}
    touch_activity(state)
    # save() was NOT called — file should not exist
    assert not state_path.exists()


def test_load_existing_state_gets_keepalive_defaults(tmp_path):
    state_path = tmp_path / "state.json"
    old_state = {"project": "OldProject", "delegation_counter": 5}
    state_path.write_text(json.dumps(old_state))
    loaded = load(state_path)
    assert loaded["last_activity_ts"] is None
    assert loaded["next_keepalive_ts"] is None
    assert loaded["keepalive_last_ts"] is None
    assert loaded["keepalive_last_status"] is None
    assert loaded["project"] == "OldProject"
    assert loaded["delegation_counter"] == 5


def test_save_persists_touch_activity(tmp_path):
    state_path = tmp_path / "state.json"
    state = load(state_path)
    now = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    touch_activity(state, now=now)
    save(state_path, state)
    reloaded = load(state_path)
    assert reloaded["last_activity_ts"] == now.isoformat()
    assert reloaded["next_keepalive_ts"] == (now + timedelta(seconds=3000)).isoformat()
