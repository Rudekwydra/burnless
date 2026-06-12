"""Tests for jsonl_doctor.py — rolling-memory transcript surgery."""

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from jsonl_doctor import (
    FLOOR_TOKENS,
    collect_tool_result_refs,
    collect_tool_use_ids,
    compact_middle,
    compressible_middle_tokens,
    current_context_tokens,
    doctor,
    load_lines,
    should_rotate,
    validate,
)


def make_entry(type_, content, usage=None, message_id=None):
    """Build a minimal JSONL-style transcript entry."""
    msg = {
        "role": type_,
        "id": message_id or f"msg_{uuid.uuid4().hex[:8]}",
        "content": content,
    }
    if usage:
        msg["usage"] = usage
    return {
        "uuid": str(uuid.uuid4()),
        "parentUuid": None,
        "sessionId": "test-session",
        "type": type_,
        "message": msg,
    }


@pytest.fixture
def fixture_lines():
    """
    Synthetic transcript (21 entries total, DEFAULT_N_TAIL=8):

    lines[0]     — root (user)
    lines[1]     — assistant: tool_use id="TU1"          [middle]
    lines[2]     — user:      tool_result tool_use_id="TU1" [middle]
    lines[3]     — assistant: tool_use id="TU2"          [middle — drag-back candidate]
    lines[4..12] — 9 misc middle entries
    lines[13]    — user: tool_result tool_use_id="TU2"   [tail — triggers drag-back]
    lines[14..19]— 6 misc tail entries
    lines[20]    — assistant: big usage                  [last; tail]

    With n_tail=8: middle = lines[1:13], tail = lines[13:21].
    Last assistant usage: input=10, cache_creation=40000, cache_read=60000 → 100010 total.
    """
    lines = []

    # root
    lines.append(make_entry("user", "Hello, please help me."))

    # TU1 use — in middle
    lines.append(make_entry(
        "assistant",
        [{"type": "tool_use", "id": "TU1", "name": "bash", "input": {"cmd": "ls"}}],
    ))

    # TU1 result — in middle (both sides in middle; neither survives to result set)
    lines.append(make_entry(
        "user",
        [{"type": "tool_result", "tool_use_id": "TU1", "content": "file_a\nfile_b"}],
    ))

    # TU2 use — in middle, must be dragged back because TU2 result is in tail
    lines.append(make_entry(
        "assistant",
        [{"type": "tool_use", "id": "TU2", "name": "read", "input": {"path": "/etc/hosts"}}],
    ))

    # 9 more middle entries
    for i in range(9):
        role = "user" if i % 2 == 0 else "assistant"
        lines.append(make_entry(role, f"Middle message {i}"))

    # lines[13]: TU2 result — in tail (triggers drag-back of TU2 use from middle)
    lines.append(make_entry(
        "user",
        [{"type": "tool_result", "tool_use_id": "TU2", "content": "127.0.0.1 localhost"}],
    ))

    # 6 more tail entries
    for i in range(6):
        role = "assistant" if i % 2 == 0 else "user"
        lines.append(make_entry(role, f"Tail message {i}"))

    # lines[20]: last assistant — big usage numbers
    lines.append(make_entry(
        "assistant",
        "Final assistant response.",
        usage={
            "input_tokens": 10,
            "cache_creation_input_tokens": 40000,
            "cache_read_input_tokens": 60000,
        },
    ))

    assert len(lines) == 21, f"Expected 21 entries, got {len(lines)}"
    return lines


# ── token measurement ────────────────────────────────────────────────────────

def test_current_context_tokens(fixture_lines):
    assert current_context_tokens(fixture_lines) == 100010


def test_compressible_middle_tokens(fixture_lines):
    assert compressible_middle_tokens(fixture_lines) == 100010 - FLOOR_TOKENS


def test_should_rotate_true(fixture_lines):
    assert should_rotate(fixture_lines, min_middle=35000) is True


def test_should_rotate_false(fixture_lines):
    assert should_rotate(fixture_lines, min_middle=200000) is False


# ── doctor + validate ─────────────────────────────────────────────────────────

def test_doctor_produces_valid_output(fixture_lines):
    entries, sid = doctor(fixture_lines)
    ok, errs = validate(entries)
    assert ok, errs


def test_no_orphan_tool_results(fixture_lines):
    entries, _ = doctor(fixture_lines)
    result_refs = collect_tool_result_refs(entries)
    use_ids = collect_tool_use_ids(entries)
    assert result_refs <= use_ids, f"Orphan refs: {result_refs - use_ids}"


def test_parent_uuid_chain(fixture_lines):
    entries, _ = doctor(fixture_lines)
    for i in range(1, len(entries)):
        assert entries[i]["parentUuid"] == entries[i - 1]["uuid"], (
            f"Chain broken at index {i}: "
            f"parentUuid={entries[i]['parentUuid']!r} != "
            f"prev uuid={entries[i - 1]['uuid']!r}"
        )


def test_middle_shrank(fixture_lines):
    entries, _ = doctor(fixture_lines)
    assert len(entries) < len(fixture_lines), (
        f"entries count {len(entries)} should be < original {len(fixture_lines)}"
    )


def test_drag_back_tool_use(fixture_lines):
    """TU2 use is in middle; TU2 result is in tail — doctor must drag TU2 use back."""
    entries, _ = doctor(fixture_lines)
    use_ids = collect_tool_use_ids(entries)
    assert "TU2" in use_ids, "TU2 tool_use was not dragged back from middle"


# ── compact_middle fallback ───────────────────────────────────────────────────

def test_compact_middle_fallback_returns_nonempty():
    """With a nonexistent ollama model the extractive fallback must return a non-empty string."""
    old = os.environ.get("BURNLESS_BRONZE_OLLAMA_MODEL")
    os.environ["BURNLESS_BRONZE_OLLAMA_MODEL"] = "nonexistent-model-xyz-99999"
    try:
        middle_entries = [
            make_entry("user", "Some middle content here."),
            make_entry("assistant", "Some response to middle content."),
        ]
        result = compact_middle(middle_entries)
        assert isinstance(result, str)
        assert len(result) > 0
    finally:
        if old is not None:
            os.environ["BURNLESS_BRONZE_OLLAMA_MODEL"] = old
        else:
            os.environ.pop("BURNLESS_BRONZE_OLLAMA_MODEL", None)


# ── load_lines ────────────────────────────────────────────────────────────────

def test_load_lines_skips_empty_and_corrupt(tmp_path):
    p = tmp_path / "test.jsonl"
    p.write_text('{"type":"user"}\n\nnot-json\n{"type":"assistant"}\n')
    lines = load_lines(str(p))
    assert len(lines) == 2
    assert lines[0]["type"] == "user"
    assert lines[1]["type"] == "assistant"
