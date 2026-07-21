from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from burnless import events as events_mod
from burnless import config as config_mod
from burnless import cli
from burnless import pure_ask as pure_ask_mod
from burnless.pure_ask import build_ask_envelope, normalize_ask_error


class TestNormalizeAskError:
    def test_normalize_ask_error_success_returns_none_none(self):
        assert normalize_ask_error(0, "ok", "") == (None, None)

    def test_normalize_ask_error_empty_stderr_is_empty_error(self):
        kind, message = normalize_ask_error(1, "", "")
        assert kind == "empty_error"
        assert message
        assert "rc=1" in message

    def test_normalize_ask_error_with_stderr_is_provider_error(self):
        kind, message = normalize_ask_error(1, "", "boom details")
        assert kind == "provider_error"
        assert message == "boom details"

    def test_normalize_ask_error_timeout(self):
        kind, _message = normalize_ask_error(1, "", "", timed_out=True)
        assert kind == "timeout"

    def test_normalize_ask_error_signal(self):
        kind, _message = normalize_ask_error(1, "", "", timed_out=False, signal=9)
        assert kind == "signal"


class TestBuildAskEnvelope:
    def _base_kwargs(self, **overrides):
        kwargs = dict(
            request_id="req-1",
            requested_tier="silver",
            effective_tier="silver",
            provider="anthropic",
            model="claude-sonnet-5",
            effort=None,
            route_source="explicit",
            route_reason="explicit --tier flag (or default)",
            route_signals=(),
            returncode=0,
            stdout="hello",
            stderr="",
        )
        kwargs.update(overrides)
        return kwargs

    def test_build_ask_envelope_ok_shape(self):
        envelope = build_ask_envelope(**self._base_kwargs())
        assert envelope["schema"] == "burnless.ask/v1"
        assert envelope["status"] == "ok"
        assert envelope["content"] == "hello"
        assert envelope["error_kind"] is None
        assert envelope["error_message"] is None
        assert set(envelope["route"].keys()) == {"source", "reason", "signals"}
        assert set(envelope["usage"].keys()) == {
            "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "basis",
        }
        assert set(envelope["cost"].keys()) == {"usd", "basis"}

    def test_build_ask_envelope_error_shape(self):
        envelope = build_ask_envelope(**self._base_kwargs(returncode=1, stdout="", stderr="boom"))
        assert envelope["status"] == "error"
        assert envelope["content"] is None
        assert envelope["error_kind"] == "provider_error"
        assert envelope["error_message"] == "boom"

    def test_build_ask_envelope_never_leaks_prompt(self):
        import inspect
        sig = inspect.signature(build_ask_envelope)
        assert "prompt" not in sig.parameters

        marker = "SECRET-PROMPT-MARKER-DO-NOT-LEAK"
        envelope = build_ask_envelope(**self._base_kwargs())
        serialized = json.dumps(envelope)
        assert marker not in serialized

    def test_build_ask_envelope_stable_across_different_raw_stdout(self):
        envelope1 = build_ask_envelope(**self._base_kwargs(stdout='{"a": 1, "b": 2}'))
        envelope2 = build_ask_envelope(**self._base_kwargs(stdout="just plain text output"))
        assert set(envelope1.keys()) == set(envelope2.keys())


class TestEventsAppendEvent:
    def test_events_append_event_returns_true_on_success(self, tmp_path: Path):
        root = tmp_path / ".burnless"
        ok = events_mod.append_event(root, "ask.started", {"request_id": "x"})
        assert ok is True

    def test_events_append_event_returns_false_on_failure(self, tmp_path: Path):
        root = tmp_path / ".burnless"
        with patch("builtins.open", side_effect=OSError("disk full")):
            ok = events_mod.append_event(root, "ask.started", {"request_id": "x"})
        assert ok is False


def _init_burnless_project(tmp_path: Path) -> Path:
    burnless = tmp_path / ".burnless"
    for d in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
        (burnless / d).mkdir(parents=True, exist_ok=True)
    config_mod.write_default(burnless / "config.yaml")
    return burnless


def _ask_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        text="hi there",
        tier="silver",
        model=None,
        provider=None,
        system=None,
        output_format="text",
        timeout=120,
        max_budget_usd=None,
        effort=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestCmdAskEnvelope:
    def test_cmd_ask_json_mode_emits_valid_envelope(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _init_burnless_project(tmp_path)

        with patch.object(pure_ask_mod, "run_ask", return_value=(0, "answer text", "")):
            rc = cli.cmd_ask(_ask_args(output_format="json"))

        assert rc == 0
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["schema"] == "burnless.ask/v1"
        assert envelope["status"] == "ok"

    def test_cmd_ask_text_mode_stays_byte_compatible(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _init_burnless_project(tmp_path)

        with patch.object(pure_ask_mod, "run_ask", return_value=(0, "answer text", "")):
            rc = cli.cmd_ask(_ask_args(output_format="text"))

        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out == "answer text\n"

    def test_cmd_ask_emits_ask_lifecycle_events(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        burnless = _init_burnless_project(tmp_path)

        with patch.object(pure_ask_mod, "run_ask", return_value=(0, "answer text", "")):
            cli.cmd_ask(_ask_args(output_format="json"))

        events_file = burnless / "events.jsonl"
        assert events_file.exists()
        lines = [json.loads(l) for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        event_types = [e["event_type"] for e in lines]

        assert "ask.started" in event_types
        assert "ask.routed" in event_types
        assert "ask.completed" in event_types
        assert event_types.index("ask.started") < event_types.index("ask.routed") < event_types.index("ask.completed")

        forbidden_keys = {"prompt", "content", "stdout", "stderr"}
        for event in lines:
            assert forbidden_keys.isdisjoint(event["data"].keys())

    def test_cmd_ask_failure_emits_ask_failed(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        burnless = _init_burnless_project(tmp_path)

        with patch.object(pure_ask_mod, "run_ask", return_value=(1, "", "boom")):
            rc = cli.cmd_ask(_ask_args(output_format="json"))

        assert rc == 1
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        assert envelope["status"] == "error"

        events_file = burnless / "events.jsonl"
        lines = [json.loads(l) for l in events_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        event_types = [e["event_type"] for e in lines]
        assert "ask.failed" in event_types
