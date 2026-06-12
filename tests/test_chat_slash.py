"""Offline tests for parse_chat_command — no network, no LLM."""
import pytest
from burnless.maestro.turn_router import parse_chat_command


def test_router_on():
    assert parse_chat_command("/router on") == ("router", True)


def test_router_off():
    assert parse_chat_command("/router off") == ("router", False)


def test_router_case_insensitive():
    assert parse_chat_command("/ROUTER ON") == ("router", True)
    assert parse_chat_command("/Router Off") == ("router", False)


def test_expand_on():
    assert parse_chat_command("/expand on") == ("expand", True)


def test_expand_off():
    assert parse_chat_command("/expand off") == ("expand", False)


def test_rollover_zero():
    assert parse_chat_command("/rollover 0") == ("rollover", 0)


def test_rollover_positive():
    assert parse_chat_command("/rollover 5") == ("rollover", 5)
    assert parse_chat_command("/rollover 100") == ("rollover", 100)


def test_rollover_invalid_string():
    result = parse_chat_command("/rollover abc")
    assert result is not None
    kind, msg = result
    assert kind == "error"
    assert "abc" in msg


def test_rollover_negative():
    result = parse_chat_command("/rollover -1")
    assert result is not None
    kind, msg = result
    assert kind == "error"


def test_status():
    assert parse_chat_command("/status") == ("status", None)


def test_help():
    assert parse_chat_command("/help") == ("help", None)


def test_exit_returns_none():
    assert parse_chat_command("/exit") is None


def test_quit_returns_none():
    assert parse_chat_command("/quit") is None


def test_q_returns_none():
    assert parse_chat_command("/q") is None


def test_unknown_slash():
    result = parse_chat_command("/foobar")
    assert result is not None
    kind, _ = result
    assert kind == "unknown"


def test_normal_line_returns_none():
    assert parse_chat_command("hello world") is None
    assert parse_chat_command("what is the weather?") is None
    assert parse_chat_command("") is None


def test_whitespace_stripped():
    assert parse_chat_command("  /router on  ") == ("router", True)
    assert parse_chat_command("  /status  ") == ("status", None)
