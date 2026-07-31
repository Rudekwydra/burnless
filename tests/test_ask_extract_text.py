"""Regression: the ask envelope's `content` must be the assistant's answer,
never the provider CLI's transport wrapper.

Before this, `content` was raw stdout: the claude CLI's JSON envelope for
anthropic (`--output-format json`) and the codex `--json` event stream for
codex. Callers had to know each provider's stream format to read a reply.
"""
import json

from burnless import pure_ask
from burnless.providers.anthropic_adapter import AnthropicAdapter
from burnless.providers.codex_adapter import CodexAdapter
from burnless.providers.ollama_adapter import OllamaAdapter
from burnless.providers.contracts import ProviderResult


ANSWER = "tier bronze resolve local, custo zero"


def _res(stdout):
    return ProviderResult(returncode=0, stdout=stdout, stderr="")


def test_anthropic_unwraps_cli_json_envelope():
    stdout = json.dumps({
        "is_error": False,
        "duration_api_ms": 176349,
        "session_id": "7fbef924-984e-4c05-a9f6-69822e8a7beb",
        "total_cost_usd": 0.769712,
        "result": ANSWER,
        "usage": {"input_tokens": 2, "output_tokens": 14790},
    })
    assert AnthropicAdapter().extract_text(_res(stdout), None) == ANSWER


def test_anthropic_passes_plain_text_through():
    assert AnthropicAdapter().extract_text(_res(ANSWER), None) == ANSWER


def test_anthropic_passes_non_result_json_through():
    stdout = json.dumps({"is_error": True, "session_id": "abc"})
    assert AnthropicAdapter().extract_text(_res(stdout), None) == stdout


def test_codex_takes_last_agent_message_from_jsonl():
    stdout = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "019fb4a5"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "item.completed",
                    "item": {"id": "item_0", "type": "reasoning", "text": "ruido"}}),
        json.dumps({"type": "item.completed",
                    "item": {"id": "item_1", "type": "agent_message", "text": ANSWER}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 59479}}),
    ])
    assert CodexAdapter().extract_text(_res(stdout), None) == ANSWER


def test_codex_survives_malformed_lines():
    stdout = "\n".join([
        "not json at all",
        "",
        json.dumps({"type": "item.completed",
                    "item": {"type": "agent_message", "text": ANSWER}}),
    ])
    assert CodexAdapter().extract_text(_res(stdout), None) == ANSWER


def test_codex_falls_back_to_stdout_when_no_agent_message():
    stdout = json.dumps({"type": "turn.completed"})
    assert CodexAdapter().extract_text(_res(stdout), None) == stdout


def test_ollama_returns_stdout_unchanged():
    assert OllamaAdapter().extract_text(_res(ANSWER), None) == ANSWER


def _envelope(**kw):
    base = dict(
        request_id="r1", requested_tier="gold", effective_tier="gold",
        provider="anthropic", model="claude-sonnet-5", effort=None,
        route_source="explicit", route_reason="explicit --tier flag",
        route_signals=(), returncode=0, stderr="",
    )
    base.update(kw)
    return pure_ask.build_ask_envelope(**base)


def test_envelope_content_is_answer_not_wrapper():
    wrapper = json.dumps({"is_error": False, "result": ANSWER})
    env = _envelope(stdout=wrapper, content_text=ANSWER)
    assert env["status"] == "ok"
    assert env["content"] == ANSWER
    assert "session_id" not in (env["content"] or "")


def test_envelope_content_text_is_optional():
    env = _envelope(stdout=ANSWER)
    assert env["content"] == ANSWER


def test_envelope_error_normalization_still_reads_raw_stdout():
    env = _envelope(stdout="", stderr="boom", returncode=1, content_text="")
    assert env["status"] == "error"
    assert env["content"] is None
