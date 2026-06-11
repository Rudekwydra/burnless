"""Tests for ollama numbered model picker in run_interactive."""
import pytest
from burnless.menu import run_interactive, list_ollama_models


class TestOllamaNumberedPicker:
    """Test the ollama model sub-picker (numbered list of installed models)."""

    def test_ollama_numbered_path(self):
        """Select ollama (provider #3) and pick model #2 from list via numbered picker."""
        # tier=silver(3), provider=ollama(3), model=2, scope=this run(1)
        inputs = iter(["3", "3", "2", "1"])
        outputs = []

        def input_fn(prompt):
            return next(inputs)

        def output_fn(msg):
            outputs.append(msg)

        models = ["gemma4:e2b", "hf.co/unsloth/gemma-4-12b-it-GGUF:Q4_K_M", "qwen2.5-coder:7b"]
        cfg = {"agents": {"silver": {"name": "haiku"}}}
        default_cfg = {"agents": {"silver": {"name": "sonnet"}}}
        providers = {"anthropic": True, "codex": True, "ollama": True}

        result = run_interactive(
            cfg,
            default_cfg,
            providers,
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=None,
            ollama_models_fn=lambda: models,
        )

        assert result is not None
        assert result["action"] == "oneshot"
        assert result["spec"] == "ollama:hf.co/unsloth/gemma-4-12b-it-GGUF:Q4_K_M"
        assert any("ollama models:" in str(o) for o in outputs)

    def test_empty_list_fallback(self):
        """When ollama has no models, type_idx=1 is the type-a-model escape."""
        # tier=silver(3), provider=ollama(3), type_idx=1(no models), type "typed-model", scope=make default(2)
        inputs = iter(["3", "3", "1", "typed-model", "2"])
        outputs = []
        persist_calls = []

        def input_fn(prompt):
            return next(inputs)

        def output_fn(msg):
            outputs.append(msg)

        def persist_fn(tier, spec):
            persist_calls.append((tier, spec))

        cfg = {"agents": {"silver": {"name": "haiku"}}}
        default_cfg = {"agents": {"silver": {"name": "sonnet"}}}
        providers = {"anthropic": True, "codex": True, "ollama": True}

        result = run_interactive(
            cfg,
            default_cfg,
            providers,
            input_fn=input_fn,
            output_fn=output_fn,
            persist_fn=persist_fn,
            ollama_models_fn=lambda: [],
        )

        assert result is not None
        assert result["action"] == "default"
        assert result["spec"] == "ollama:typed-model"
        assert persist_calls == [("silver", "ollama:typed-model")]

    def test_list_ollama_models_live(self):
        """list_ollama_models() returns a list (live call, just type check)."""
        result = list_ollama_models()
        assert isinstance(result, list)
