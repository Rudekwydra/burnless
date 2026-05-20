"""Tests for maestro_layer (unit; subprocess mocked)."""
import json
from burnless.maestro_layer import (
    MAESTRO_HARD_RULES,
    _build_user_message,
    _parse_stream_json,
    _try_extract_envelope_json,
)


def test_hard_rules_present():
    rules = MAESTRO_HARD_RULES.lower()
    assert "never read files directly" in rules
    assert "delegate" in rules
    assert "burnless do" in rules
    assert "escapulida" in rules


def test_user_message_includes_envelope_and_rules():
    msg = _build_user_message("intent=test", "tight")
    assert "MAESTRO ROLE" in msg
    assert "intent=test" in msg
    assert "tight" in msg
    assert "[INCOMING ENVELOPE FROM ENCODER]" in msg


def test_parse_stream_json_extracts_session_and_result():
    lines = [
        '{"type": "system", "session_id": "abc-123", "subtype": "init"}',
        '{"type": "stream_event", "event": {"type": "message_start"}}',
        '{"type": "result", "subtype": "success", "result": "{\\"response_envelope\\": \\"done\\"}"}',
    ]
    session, text = _parse_stream_json("\n".join(lines))
    assert session == "abc-123"
    assert "response_envelope" in text


def test_parse_stream_json_handles_empty():
    session, text = _parse_stream_json("")
    assert session is None
    assert text == ""


def test_parse_stream_json_ignores_malformed_lines():
    lines = [
        '{"type": "system", "session_id": "s-1"}',
        'not json',
        '{"type": "result", "result": "hello"}',
    ]
    session, text = _parse_stream_json("\n".join(lines))
    assert session == "s-1"
    assert text == "hello"


def test_extract_envelope_fenced_json():
    text = 'thinking...\n```json\n{"response_envelope": "OK", "next": ""}\n```'
    env = _try_extract_envelope_json(text)
    assert env == {"response_envelope": "OK", "next": ""}


def test_extract_envelope_trailing_json():
    text = 'reasoning...\n\nFinal:\n{"response_envelope": "fixed bug"}'
    env = _try_extract_envelope_json(text)
    assert env == {"response_envelope": "fixed bug"}


def test_extract_envelope_returns_none_when_no_json():
    assert _try_extract_envelope_json("plain text no json") is None
    assert _try_extract_envelope_json("") is None
