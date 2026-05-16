"""Tests for chat_mode improvements: streaming, history trimming, minification, slash commands."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from contextlib import contextmanager

import pytest

from burnless import chat_mode, paths, state as state_mod, config as config_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_paths(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
        p[key].mkdir(parents=True, exist_ok=True)
    p["config"].write_text(
        """
project_name: test
agents:
  gold:
    name: opus
    command: printf ok
    role: strategy
  silver:
    name: sonnet
    command: printf ok
    role: execution
  bronze:
    name: haiku
    command: printf ok
    role: cheap
""",
        encoding="utf-8",
    )
    state_mod.save(p["state"], state_mod.DEFAULT_STATE | {"project": "test"})
    return p


# ---------------------------------------------------------------------------
# build_prompt (subprocess path — kept for backwards compat)
# ---------------------------------------------------------------------------

def test_build_prompt_includes_user_message(tmp_path):
    p = _make_paths(tmp_path)
    result = chat_mode.build_prompt(p, "status check", [])
    assert "status check" in result


def test_build_prompt_trims_to_default_history_turns(tmp_path):
    p = _make_paths(tmp_path)
    long_turns = [{"user": f"msg{i}", "assistant": f"resp{i}"} for i in range(20)]
    result = chat_mode.build_prompt(p, "now", long_turns)
    # Only last DEFAULT_HISTORY_TURNS turns should appear
    assert "msg9" not in result   # turn 9 is outside the window of last-10 from 20 turns
    assert "msg19" in result      # last turn is always present


def test_build_prompt_includes_plan_when_present(tmp_path):
    p = _make_paths(tmp_path)
    st = state_mod.load(p["state"])
    st["plan"] = "implement feature X"
    state_mod.save(p["state"], st)
    result = chat_mode.build_prompt(p, "go", [])
    assert "implement feature X" in result


# ---------------------------------------------------------------------------
# _load_memory
# ---------------------------------------------------------------------------

def test_load_memory_reads_memory_file(tmp_path):
    p = _make_paths(tmp_path)
    memory_file = p["root"].parent / "MEMORY.md"
    memory_file.write_text("# project memory\nkey fact here\n", encoding="utf-8")
    blob = chat_mode._load_memory(p)
    assert "key fact here" in blob


def test_load_memory_empty_when_no_files(tmp_path):
    p = _make_paths(tmp_path)
    blob = chat_mode._load_memory(p)
    assert blob == ""


# ---------------------------------------------------------------------------
# _CHAT_HELP constant
# ---------------------------------------------------------------------------

def test_chat_help_lists_key_commands():
    for cmd in ("/help", "/clear", "/model", "/info", "/exit"):
        assert cmd in chat_mode._CHAT_HELP


# ---------------------------------------------------------------------------
# minify integration: encoder.minify strips fillers from user input
# ---------------------------------------------------------------------------

def test_minify_strips_portuguese_filler():
    from burnless.codec.encoder import minify
    result = minify("por favor implementa o teste")
    assert "por favor" not in result.lower()
    assert "implementa" in result


def test_minify_strips_english_filler():
    from burnless.codec.encoder import minify
    result = minify("can you please fix the auth module?")
    assert "can you" not in result.lower()
    assert "fix" in result


def test_minify_preserves_technical_content():
    from burnless.codec.encoder import minify
    result = minify("check the state of delegations d010 and d011")
    assert "d010" in result
    assert "d011" in result


# ---------------------------------------------------------------------------
# _run_chat_sdk: slash commands and history trimming via input() mocking
# ---------------------------------------------------------------------------

def _make_stream_mock(chunks: list[str], cache_read: int = 0, cache_write: int = 50):
    """Build a mock for client.messages.stream() context manager."""
    usage = MagicMock()
    usage.cache_read_input_tokens = cache_read
    usage.cache_creation_input_tokens = cache_write
    usage.input_tokens = 100

    final_msg = MagicMock()
    final_msg.usage = usage

    stream = MagicMock()
    stream.text_stream = iter(chunks)
    stream.get_final_message.return_value = final_msg

    @contextmanager
    def _stream_ctx(*args, **kwargs):
        yield stream

    client = MagicMock()
    client.messages.stream.side_effect = _stream_ctx
    return client


def _make_anthropic_module(client):
    """Mock anthropic module with the given client."""
    mod = MagicMock()
    mod.Anthropic.return_value = client
    return mod


def test_slash_exit_returns_0(tmp_path, monkeypatch, capsys):
    p = _make_paths(tmp_path)
    client = _make_stream_mock(["hello"])

    with patch("builtins.input", side_effect=["/exit"]):
        with patch.dict("sys.modules", {"anthropic": _make_anthropic_module(client)}):
            result = chat_mode._run_chat_sdk(p, "gold", {"name": "opus"}, "sk-fake", p["chat"] / "chat.jsonl")
    assert result == 0


def test_slash_help_prints_help(tmp_path, monkeypatch, capsys):
    p = _make_paths(tmp_path)
    client = _make_stream_mock(["hi"])

    with patch("builtins.input", side_effect=["/help", EOFError]):
        with patch.dict("sys.modules", {"anthropic": _make_anthropic_module(client)}):
            chat_mode._run_chat_sdk(p, "gold", {"name": "opus"}, "sk-fake", p["chat"] / "chat.jsonl")

    out = capsys.readouterr().out
    assert "/model" in out
    assert "/info" in out


def test_slash_model_switches_model(tmp_path, capsys):
    p = _make_paths(tmp_path)
    client = _make_stream_mock(["ok"])

    inputs = iter(["/model claude-haiku-4-5-20251001", "hello", EOFError()])

    def fake_input(prompt=""):
        val = next(inputs)
        if isinstance(val, type) and issubclass(val, Exception):
            raise val()
        if isinstance(val, BaseException):
            raise val
        return val

    with patch("builtins.input", side_effect=fake_input):
        with patch.dict("sys.modules", {"anthropic": _make_anthropic_module(client)}):
            chat_mode._run_chat_sdk(p, "gold", {"name": "opus"}, "sk-fake", p["chat"] / "chat.jsonl")

    out = capsys.readouterr().out
    assert "claude-haiku-4-5-20251001" in out
    # stream was called with the new model
    call_kwargs = client.messages.stream.call_args
    assert call_kwargs.kwargs.get("model") == "claude-haiku-4-5-20251001"


def test_slash_info_shows_turn_count(tmp_path, capsys):
    p = _make_paths(tmp_path)
    client = _make_stream_mock(["response text"])

    inputs = iter(["hello world", "/info", EOFError()])

    def fake_input(prompt=""):
        val = next(inputs)
        if isinstance(val, BaseException):
            raise val
        return val

    with patch("builtins.input", side_effect=fake_input):
        with patch.dict("sys.modules", {"anthropic": _make_anthropic_module(client)}):
            chat_mode._run_chat_sdk(p, "gold", {"name": "opus"}, "sk-fake", p["chat"] / "chat.jsonl")

    out = capsys.readouterr().out
    # After 1 full turn, /info should show turns: 1
    assert "turns: 1" in out


def test_slash_unknown_prints_error(tmp_path, capsys):
    p = _make_paths(tmp_path)
    client = _make_stream_mock(["hi"])

    with patch("builtins.input", side_effect=["/nonexistent", EOFError()]):
        with patch.dict("sys.modules", {"anthropic": _make_anthropic_module(client)}):
            chat_mode._run_chat_sdk(p, "gold", {"name": "opus"}, "sk-fake", p["chat"] / "chat.jsonl")

    out = capsys.readouterr().out
    assert "unknown command" in out
    assert "/help" in out


def test_slash_clear_resets_history(tmp_path, capsys):
    p = _make_paths(tmp_path)
    client = _make_stream_mock(["answer"])

    inputs = iter(["first message", "/clear", "second message", EOFError()])

    def fake_input(prompt=""):
        val = next(inputs)
        if isinstance(val, BaseException):
            raise val
        return val

    with patch("builtins.input", side_effect=fake_input):
        with patch.dict("sys.modules", {"anthropic": _make_anthropic_module(client)}):
            chat_mode._run_chat_sdk(p, "gold", {"name": "opus"}, "sk-fake", p["chat"] / "chat.jsonl")

    # stream called twice (first message + second message after clear)
    assert client.messages.stream.call_count == 2
    # After clear, second call should have only 1 message in history (just "second message")
    second_call_kwargs = client.messages.stream.call_args_list[1].kwargs
    assert len(second_call_kwargs["messages"]) == 1


# ---------------------------------------------------------------------------
# History trimming: only last DEFAULT_HISTORY_TURNS*2 messages sent to API
# ---------------------------------------------------------------------------

def test_history_trimmed_before_api_call(tmp_path):
    """After more than DEFAULT_HISTORY_TURNS turns, API only receives last N*2 messages."""
    p = _make_paths(tmp_path)
    client = _make_stream_mock(["ok"])

    n = chat_mode.DEFAULT_HISTORY_TURNS  # 10
    # Feed n+2 messages then exit
    msgs = [f"msg{i}" for i in range(n + 2)] + [EOFError()]

    def fake_input(prompt=""):
        val = msgs.pop(0)
        if isinstance(val, BaseException):
            raise val
        return val

    with patch("builtins.input", side_effect=fake_input):
        with patch.dict("sys.modules", {"anthropic": _make_anthropic_module(client)}):
            chat_mode._run_chat_sdk(p, "gold", {"name": "opus"}, "sk-fake", p["chat"] / "chat.jsonl")

    # Last API call should have at most n*2 messages
    last_call_kwargs = client.messages.stream.call_args_list[-1].kwargs
    assert len(last_call_kwargs["messages"]) <= n * 2


# ---------------------------------------------------------------------------
# Streaming: output written to stdout incrementally (not all-at-once)
# ---------------------------------------------------------------------------

def test_streaming_output_printed(tmp_path, capsys):
    p = _make_paths(tmp_path)
    chunks = ["Hello", ", ", "world", "!"]
    client = _make_stream_mock(chunks)

    with patch("builtins.input", side_effect=["test message", EOFError()]):
        with patch.dict("sys.modules", {"anthropic": _make_anthropic_module(client)}):
            chat_mode._run_chat_sdk(p, "gold", {"name": "opus"}, "sk-fake", p["chat"] / "chat.jsonl")

    out = capsys.readouterr().out
    assert "Hello, world!" in out


# ---------------------------------------------------------------------------
# JSONL logging: turn logged with correct fields
# ---------------------------------------------------------------------------

def test_turn_logged_to_jsonl(tmp_path):
    p = _make_paths(tmp_path)
    client = _make_stream_mock(["the answer"])
    log_path = p["chat"] / "chat.jsonl"

    with patch("builtins.input", side_effect=["what is 2+2?", EOFError()]):
        with patch.dict("sys.modules", {"anthropic": _make_anthropic_module(client)}):
            chat_mode._run_chat_sdk(p, "gold", {"name": "opus"}, "sk-fake", log_path)

    lines = [json.loads(l) for l in log_path.read_text().splitlines()]
    assert len(lines) == 1
    record = lines[0]
    assert record["backend"] == "sdk"
    assert record["assistant"] == "the answer"
    assert "ts" in record
