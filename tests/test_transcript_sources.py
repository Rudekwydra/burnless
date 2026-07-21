import json
from pathlib import Path

import pytest

from burnless import transcript_sources


def _write_jsonl(path: Path, lines) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            if isinstance(line, str):
                f.write(line + "\n")
            else:
                f.write(json.dumps(line) + "\n")


def _codex_uuid7(year: int, month: int, day: int, hour: int = 12) -> str:
    import datetime

    dt = datetime.datetime(year, month, day, hour, 0, 0, tzinfo=datetime.timezone.utc)
    ms = int(dt.timestamp() * 1000)
    ts_hex = format(ms, "012x")
    rest = "7abc9def012345678901"
    hex32 = ts_hex + rest
    assert len(hex32) == 32
    return f"{hex32[0:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:32]}"


# ---------------------------------------------------------------------------
# resolve_path — claude
# ---------------------------------------------------------------------------


def test_resolve_path_claude_slug_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    sid = "11111111-2222-3333-4444-555555555555"
    cwd = "/Users/roberto/antigravity/burnless"
    slug = cwd.replace("/", "-").replace(".", "-")
    transcript = tmp_path / ".claude" / "projects" / slug / f"{sid}.jsonl"
    _write_jsonl(transcript, [{"type": "summary", "summary": "hi"}])

    result = transcript_sources.resolve_path("claude", sid, cwd)

    assert result == transcript


def test_resolve_path_claude_glob_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    sid = "11111111-2222-3333-4444-555555555555"
    real_cwd = "/Users/roberto/antigravity/burnless"
    slug = real_cwd.replace("/", "-").replace(".", "-")
    transcript = tmp_path / ".claude" / "projects" / slug / f"{sid}.jsonl"
    _write_jsonl(transcript, [{"type": "summary", "summary": "hi"}])

    result = transcript_sources.resolve_path("claude", sid, "/some/other/cwd")

    assert result == transcript


def test_resolve_path_claude_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = transcript_sources.resolve_path("claude", "no-such-sid", "/whatever")

    assert result is None


# ---------------------------------------------------------------------------
# resolve_path — codex
# ---------------------------------------------------------------------------


def test_resolve_path_codex_uuidv7_decode(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    sid = _codex_uuid7(2026, 7, 20)
    day_dir = tmp_path / ".codex" / "sessions" / "2026" / "07" / "20"
    transcript = day_dir / f"rollout-2026-07-20T12-00-00-{sid}.jsonl"
    _write_jsonl(transcript, [{"type": "session_meta", "payload": {"session_id": sid}}])

    result = transcript_sources.resolve_path("codex", sid, None)

    assert result == transcript


def test_resolve_path_codex_timezone_tolerant(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    sid = _codex_uuid7(2026, 7, 20)
    # File lives one day off from what the UUID decodes to.
    day_dir = tmp_path / ".codex" / "sessions" / "2026" / "07" / "21"
    transcript = day_dir / f"rollout-2026-07-21T00-30-00-{sid}.jsonl"
    _write_jsonl(transcript, [{"type": "session_meta", "payload": {"session_id": sid}}])

    result = transcript_sources.resolve_path("codex", sid, None)

    assert result == transcript


def test_resolve_path_codex_rglob_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    sid = _codex_uuid7(2026, 7, 20)
    # Way off from the UUID-decoded date — D-1/D/D+1 dirs won't exist.
    day_dir = tmp_path / ".codex" / "sessions" / "2019" / "01" / "01"
    transcript = day_dir / f"rollout-2019-01-01T00-00-00-{sid}.jsonl"
    _write_jsonl(transcript, [{"type": "session_meta", "payload": {"session_id": sid}}])

    result = transcript_sources.resolve_path("codex", sid, None)

    assert result == transcript


def test_resolve_path_unknown_host_raises():
    with pytest.raises(ValueError):
        transcript_sources.resolve_path("bogus", "x", None)


# ---------------------------------------------------------------------------
# iter_turns — codex
# ---------------------------------------------------------------------------


def test_iter_turns_codex_whitelist(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_jsonl(path, [
        {"type": "session_meta", "payload": {"session_id": "s1"}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "dev noise"}]}},
        {"type": "response_item", "payload": {"type": "reasoning", "content": "thinking..."}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Real question one"}]}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "shell"}},
        {"type": "response_item", "payload": {"type": "function_call_output", "output": "ok"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Real reply one"}]}},
    ])

    turns = list(transcript_sources.iter_turns("codex", path))

    assert turns == [
        {"role": "user", "text": "Real question one"},
        {"role": "assistant", "text": "Real reply one"},
    ]


def test_iter_turns_codex_strips_boot_noise(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_jsonl(path, [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>\n  <cwd>/tmp</cwd>\n</environment_context>"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Real question"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Real reply"}]}},
    ])

    turns = list(transcript_sources.iter_turns("codex", path))

    assert turns == [
        {"role": "user", "text": "Real question"},
        {"role": "assistant", "text": "Real reply"},
    ]


def test_iter_turns_codex_compacted_marker_skipped_not_disruptive(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_jsonl(path, [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "before compaction"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "reply before"}]}},
        {"type": "compacted", "payload": {"message": "", "replacement_history": []}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "after compaction"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "reply after"}]}},
    ])

    turns = list(transcript_sources.iter_turns("codex", path))

    assert turns == [
        {"role": "user", "text": "before compaction"},
        {"role": "assistant", "text": "reply before"},
        {"role": "user", "text": "after compaction"},
        {"role": "assistant", "text": "reply after"},
    ]


def test_iter_turns_codex_malformed_line_skipped(tmp_path):
    path = tmp_path / "session.jsonl"
    _write_jsonl(path, [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "before bad line"}]}},
        "{not valid json!!",
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "after bad line"}]}},
    ])

    turns = list(transcript_sources.iter_turns("codex", path))

    assert turns == [
        {"role": "user", "text": "before bad line"},
        {"role": "assistant", "text": "after bad line"},
    ]


def test_iter_turns_missing_file_raises():
    with pytest.raises(OSError):
        list(transcript_sources.iter_turns("codex", Path("/nonexistent/x.jsonl")))


def test_iter_turns_unknown_host_raises(tmp_path):
    with pytest.raises(ValueError):
        transcript_sources.iter_turns("bogus", tmp_path / "whatever.jsonl")
