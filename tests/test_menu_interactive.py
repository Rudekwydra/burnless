"""Tests for burnless.menu interactive picker."""

import pytest
from burnless.menu import run_interactive, worker_menu_options


class TestWorkerMenuOptions:
    """Test worker_menu_options function."""

    def test_returns_list_of_three(self):
        providers = {"anthropic": True, "codex": False, "ollama": True}
        opts = worker_menu_options(providers)
        assert isinstance(opts, list)
        assert len(opts) == 3

    def test_provider_order(self):
        providers = {"anthropic": True, "codex": True, "ollama": True}
        opts = worker_menu_options(providers)
        assert opts[0]["provider"] == "anthropic"
        assert opts[1]["provider"] == "codex"
        assert opts[2]["provider"] == "ollama"

    def test_anthropic_available_true(self):
        providers = {"anthropic": True, "codex": False, "ollama": False}
        opts = worker_menu_options(providers)
        anthropic_opt = next(o for o in opts if o["provider"] == "anthropic")
        assert anthropic_opt["available"] is True

    def test_codex_availability_respects_providers(self):
        providers = {"anthropic": True, "codex": False, "ollama": True}
        opts = worker_menu_options(providers)
        codex_opt = next((o for o in opts if o["provider"] == "codex"), None)
        assert codex_opt is not None
        assert codex_opt["available"] is False

    def test_ollama_available_true(self):
        providers = {"anthropic": True, "codex": False, "ollama": True}
        opts = worker_menu_options(providers)
        ollama_opt = next(o for o in opts if o["provider"] == "ollama")
        assert ollama_opt["available"] is True


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
            providers={"anthropic": True, "codex": True, "ollama": True},
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
            providers={"anthropic": True, "codex": True, "ollama": True},
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=None,
        )

        assert result is None


class TestRunInteractiveMakeDefault:
    """Test run_interactive make-default path."""

    def test_make_default_silver_sonnet(self):
        # Tier 3=silver, provider 1=anthropic, model 3=sonnet (in ["opus","fable","sonnet","haiku"]), scope 2=make default
        it = iter(["3", "1", "3", "2"])
        input_fn = lambda prompt="": next(it)
        outputs = []
        output_fn = lambda msg: outputs.append(msg)
        persist_calls = []

        def persist_fn(tier, spec):
            persist_calls.append((tier, spec))

        result = run_interactive(
            cfg={"agents": {"silver": {"name": "haiku"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}},
            providers={"anthropic": True, "codex": True, "ollama": True},
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=persist_fn,
            anthropic_models_fn=lambda: ["opus", "fable", "sonnet", "haiku"],
        )

        assert result == {"action": "default", "tier": "silver", "spec": "anthropic:sonnet"}
        assert persist_calls == [("silver", "anthropic:sonnet")]

    def test_make_default_persist_not_called_when_none(self):
        # Tier 3=silver, provider 1=anthropic, model 3=sonnet, scope 2=make default
        it = iter(["3", "1", "3", "2"])
        input_fn = lambda prompt="": next(it)
        outputs = []
        output_fn = lambda msg: outputs.append(msg)

        result = run_interactive(
            cfg={"agents": {"silver": {"name": "haiku"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}},
            providers={"anthropic": True, "codex": True, "ollama": True},
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=None,
            anthropic_models_fn=lambda: ["opus", "fable", "sonnet", "haiku"],
        )

        assert result == {"action": "default", "tier": "silver", "spec": "anthropic:sonnet"}


class TestRunInteractiveOllama:
    """Test run_interactive ollama custom model path."""

    def test_ollama_custom_model_this_run(self):
        # Tier 3=silver, provider 3=ollama, no models→type_idx=1→type model, scope 1=this run
        it = iter(["3", "3", "1", "gemma4:e2b", "1"])
        input_fn = lambda prompt="": next(it)
        outputs = []
        output_fn = lambda msg: outputs.append(msg)
        persist_calls = []

        def persist_fn(tier, spec):
            persist_calls.append((tier, spec))

        result = run_interactive(
            cfg={"agents": {"silver": {"name": "haiku"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}},
            providers={"anthropic": True, "codex": True, "ollama": True},
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=persist_fn,
            ollama_models_fn=lambda: [],
        )

        assert result == {"action": "oneshot", "tier": "silver", "spec": "ollama:gemma4:e2b"}
        assert persist_calls == []

    def test_ollama_custom_empty_model_returns_none(self):
        # Tier 3=silver, provider 3=ollama, no models→type_idx=1→empty model→None
        it = iter(["3", "3", "1", ""])
        input_fn = lambda prompt="": next(it)
        outputs = []
        output_fn = lambda msg: outputs.append(msg)

        result = run_interactive(
            cfg={"agents": {"silver": {"name": "haiku"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}},
            providers={"anthropic": True, "codex": True, "ollama": True},
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=None,
            ollama_models_fn=lambda: [],
        )

        assert result is None
