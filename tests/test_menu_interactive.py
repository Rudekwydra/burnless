"""Tests for burnless.menu interactive picker."""

import pytest
from burnless.menu import run_interactive, worker_menu_options


class TestWorkerMenuOptions:
    """Test worker_menu_options function."""

    def test_returns_list(self):
        providers = {"anthropic": True, "codex": False, "gemini": True, "ollama": True}
        opts = worker_menu_options(providers)
        assert isinstance(opts, list)
        assert len(opts) > 0

    def test_anthropic_models_present(self):
        providers = {"anthropic": True, "codex": False, "gemini": False, "ollama": False}
        opts = worker_menu_options(providers)
        anthropic_opts = [o for o in opts if o["provider"] == "anthropic"]
        assert len(anthropic_opts) == 3
        models = [o["model"] for o in anthropic_opts]
        assert "opus" in models
        assert "sonnet" in models
        assert "haiku" in models

    def test_codex_availability_respects_providers(self):
        providers = {"anthropic": True, "codex": False, "gemini": True, "ollama": True}
        opts = worker_menu_options(providers)
        codex_opt = next((o for o in opts if o["provider"] == "codex"), None)
        assert codex_opt is not None
        assert codex_opt["available"] is False

    def test_ollama_is_custom(self):
        providers = {"anthropic": True, "codex": False, "gemini": True, "ollama": True}
        opts = worker_menu_options(providers)
        ollama_opt = next((o for o in opts if o["provider"] == "ollama"), None)
        assert ollama_opt is not None
        assert ollama_opt["custom"] is True
        assert ollama_opt["spec"] == "ollama:"

    def test_anthropic_not_custom(self):
        providers = {"anthropic": True, "codex": False, "gemini": False, "ollama": False}
        opts = worker_menu_options(providers)
        anthropic_opts = [o for o in opts if o["provider"] == "anthropic"]
        for opt in anthropic_opts:
            assert opt["custom"] is False

    def test_exactly_one_custom_option(self):
        providers = {"anthropic": True, "codex": False, "gemini": True, "ollama": True}
        opts = worker_menu_options(providers)
        custom_opts = [o for o in opts if o["custom"]]
        assert len(custom_opts) == 1
        assert custom_opts[0]["provider"] == "ollama"


class TestRunInteractiveQuitPath:
    """Test run_interactive quit path."""

    def test_quit_on_empty_tier_input(self):
        it = iter([""])
        input_fn = lambda prompt="": next(it)
        outputs = []
        output_fn = lambda msg: outputs.append(msg)

        result = run_interactive(
            cfg={"agents": {"silver": {"name": "haiku"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}},
            providers={"anthropic": True, "codex": True, "gemini": True, "ollama": True},
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=None,
        )

        assert result is None

    def test_quit_on_q_tier_input(self):
        it = iter(["q"])
        input_fn = lambda prompt="": next(it)
        outputs = []
        output_fn = lambda msg: outputs.append(msg)

        result = run_interactive(
            cfg={"agents": {"silver": {"name": "haiku"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}},
            providers={"anthropic": True, "codex": True, "gemini": True, "ollama": True},
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=None,
        )

        assert result is None


class TestRunInteractiveMakeDefault:
    """Test run_interactive make-default path."""

    def test_make_default_silver_sonnet(self):
        # Tier 3 = silver, worker 2 = anthropic:sonnet, scope 2 = make default
        it = iter(["3", "2", "2"])
        input_fn = lambda prompt="": next(it)
        outputs = []
        output_fn = lambda msg: outputs.append(msg)
        persist_calls = []

        def persist_fn(tier, spec):
            persist_calls.append((tier, spec))

        result = run_interactive(
            cfg={"agents": {"silver": {"name": "haiku"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}},
            providers={"anthropic": True, "codex": True, "gemini": True, "ollama": True},
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=persist_fn,
        )

        assert result == {"action": "default", "tier": "silver", "spec": "anthropic:sonnet"}
        assert persist_calls == [("silver", "anthropic:sonnet")]

    def test_make_default_persist_not_called_when_none(self):
        # Tier 3 = silver, worker 2 = anthropic:sonnet, scope 2 = make default
        it = iter(["3", "2", "2"])
        input_fn = lambda prompt="": next(it)
        outputs = []
        output_fn = lambda msg: outputs.append(msg)

        result = run_interactive(
            cfg={"agents": {"silver": {"name": "haiku"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}},
            providers={"anthropic": True, "codex": True, "gemini": True, "ollama": True},
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=None,
        )

        assert result == {"action": "default", "tier": "silver", "spec": "anthropic:sonnet"}


class TestRunInteractiveOllama:
    """Test run_interactive ollama custom model path."""

    def test_ollama_custom_model_this_run(self):
        # Tier 3 = silver, worker 6 = ollama custom, type model, scope 1 = this run
        it = iter(["3", "6", "gemma4:e2b", "1"])
        input_fn = lambda prompt="": next(it)
        outputs = []
        output_fn = lambda msg: outputs.append(msg)
        persist_calls = []

        def persist_fn(tier, spec):
            persist_calls.append((tier, spec))

        result = run_interactive(
            cfg={"agents": {"silver": {"name": "haiku"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}},
            providers={"anthropic": True, "codex": True, "gemini": True, "ollama": True},
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=persist_fn,
        )

        assert result == {"action": "oneshot", "tier": "silver", "spec": "ollama:gemma4:e2b"}
        assert persist_calls == []

    def test_ollama_custom_empty_model_returns_none(self):
        # Tier 3 = silver, worker 6 = ollama custom, empty model input
        it = iter(["3", "6", ""])
        input_fn = lambda prompt="": next(it)
        outputs = []
        output_fn = lambda msg: outputs.append(msg)

        result = run_interactive(
            cfg={"agents": {"silver": {"name": "haiku"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}},
            providers={"anthropic": True, "codex": True, "gemini": True, "ollama": True},
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=None,
        )

        assert result is None
