"""Tests for anthropic_models() and run_interactive anthropic path."""
import pytest
from burnless.menu import anthropic_models, run_interactive


def test_anthropic_models_nonempty():
    result = anthropic_models()
    assert isinstance(result, list)
    assert len(result) > 0


def test_anthropic_models_curated_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = anthropic_models()
    assert result == ["opus", "fable", "sonnet", "haiku"]


def test_run_interactive_anthropic_this_run():
    # tier=silver(3), provider=anthropic(1), model=2=sonnet (from ["opus","sonnet"]), scope=this run(1)
    inputs = iter(["3", "1", "2", "1"])

    def input_fn(prompt=""):
        return next(inputs)

    outputs = []

    def output_fn(msg):
        outputs.append(msg)

    result = run_interactive(
        cfg={"agents": {"silver": {"name": "haiku"}}},
        default_cfg={"agents": {"silver": {"name": "sonnet"}}},
        providers={"anthropic": True, "codex": False, "ollama": False},
        input_fn=input_fn,
        output_fn=output_fn,
        persist_fn=None,
        anthropic_models_fn=lambda: ["opus", "sonnet"],
    )

    assert result == {"action": "oneshot", "tier": "silver", "spec": "anthropic:sonnet"}
