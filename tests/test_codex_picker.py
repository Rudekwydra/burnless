import os
import pytest
from burnless.menu import list_codex_models, worker_menu_options, run_interactive

def test_list_codex_models():
    assert isinstance(list_codex_models(), list)

def test_worker_menu_options_codex_entry():
    providers = {"anthropic": False, "codex": True, "ollama": False}
    opts = worker_menu_options(providers)
    codex_opt = next((o for o in opts if o["provider"] == "codex"), None)
    assert codex_opt is not None
    assert codex_opt["available"] is True

def test_run_interactive_codex_numbered():
    providers = {
        "anthropic": True,
        "codex": True,
        "ollama": True,
    }
    # Provider order: 1=anthropic, 2=codex, 3=ollama
    # inputs: tier=silver(3), provider=codex(2), model=1(gpt-5.5), scope=this run(1)
    inputs = iter(["3", "2", "1", "1"])
    def input_fn(prompt):
        return next(inputs)

    outputs = []
    def output_fn(msg):
        outputs.append(msg)

    cfg = {"agents": {"silver": {"name": "haiku"}}}
    default_cfg = {"agents": {"silver": {"name": "sonnet"}}}

    result = run_interactive(
        cfg, default_cfg, providers,
        input_fn=input_fn,
        output_fn=output_fn,
        codex_models_fn=lambda: ["gpt-5.5", "o3"],
    )

    assert result["action"] == "oneshot"
    assert result["tier"] == "silver"
    assert result["spec"] == "codex:gpt-5.5"
