import pytest
import json
from pathlib import Path
from burnless.events import append_event, read_events


def test_roundtrip(tmp_path):
	root = tmp_path

	append_event(root, "test_event", {"msg": "first"})
	append_event(root, "test_event", {"msg": "second"})

	events = read_events(root)
	assert len(events) == 2
	assert events[0]["event_type"] == "test_event"
	assert events[0]["data"] == {"msg": "first"}
	assert events[1]["event_type"] == "test_event"
	assert events[1]["data"] == {"msg": "second"}


def test_malformed_line(tmp_path):
	root = tmp_path
	events_file = root / "events.jsonl"

	events_file.write_text("not valid json\n", encoding='utf-8')

	append_event(root, "valid_event", {"key": "value"})

	events = read_events(root)
	assert len(events) == 1
	assert events[0]["event_type"] == "valid_event"
	assert events[0]["data"] == {"key": "value"}


def test_event_type_filter(tmp_path):
	root = tmp_path

	append_event(root, "type_a", {"data": 1})
	append_event(root, "type_b", {"data": 2})
	append_event(root, "type_a", {"data": 3})

	events_a = read_events(root, event_type="type_a")
	assert len(events_a) == 2
	assert all(e["event_type"] == "type_a" for e in events_a)

	events_b = read_events(root, event_type="type_b")
	assert len(events_b) == 1
	assert events_b[0]["event_type"] == "type_b"


def test_limit(tmp_path):
	root = tmp_path

	for i in range(5):
		append_event(root, "event", {"idx": i})

	events = read_events(root, limit=3)
	assert len(events) == 3
	assert events[0]["data"]["idx"] == 2
	assert events[1]["data"]["idx"] == 3
	assert events[2]["data"]["idx"] == 4


def test_scope_auto_built(tmp_path):
	root = tmp_path

	append_event(root, "event", {}, scope=None)

	events = read_events(root)
	assert len(events) == 1

	scope = events[0]["scope"]
	assert scope["project_root"].startswith("/")
	assert scope["project_root_hash"].startswith("sha256:")
	assert scope["session_id"] is None


def test_never_raises(tmp_path):
	fake_root = tmp_path / "fake_root"
	fake_root.write_text("i am a file", encoding='utf-8')

	result = append_event(fake_root, "event", {})
	assert result is False
