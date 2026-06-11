import os
import tomllib
import pytest
from pathlib import Path
from burnless.menu import list_codex_models, worker_menu_options, run_interactive

def test_list_codex_models_success():
    tmp_path = Path("/tmp/test_codex_config.toml")
    content = 'model = "gpt-5.4-mini"\n[tui.model_availability_nux]\n"gpt-5.5" = 4\n'
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    try:
        models = list_codex_models(str(tmp_path))
        assert models == ["gpt-5.4-mini", "gpt-5.5"]
    finally:
        if tmp_path.exists():
            os.remove(tmp_path)

def test_list_codex_models_missing():
    assert list_codex_models("/nonexistent/x.toml") == []

def test_worker_menu_options_codex_custom():
    providers = {"codex": True}
    opts = worker_menu_options(providers)
    # Find the codex entry (it was appended in a specific order but let's check for 'codex')
    codex_opt = next((o for o in opts if o["provider"] == "codex"), None)
    assert codex_opt is not None
    assert codex_opt["custom"] is True
    assert codex_opt["model"] == "(pick installed)"

def test_run_interactive_codex_numbered():
    providers = {
        "anthropic": True, 
        "codex": True, 
        "gemini": True, 
        "ollama": True
    }
    # Order in worker_menu_options:
    # 0: anthropic:fable
    # 1: anthropic:opus
    # 2: anthropic:sonnet
    # 3: anthropic:haiku
    # 4: codex:(pick installed)  <-- index 5 (worker selection input "5")
    # 5: gemini:gemini-2.5-pro
    # 6: ollama:(type a model)
    
    # inputs iter ["3","5","1","1"]
    # 1. Tier [1-4, q]: "3" -> silver
    # 2. Worker [1-7, q]: "5" -> codex (custom=True)
    # 3. Model [1-2, q]: "1" -> gpt-5.5
    # 4. Apply: [1] this run [2] make default [q]: "1" -> oneshot
    
    inputs = iter(["3", "5", "1", "1"])
    def input_fn(prompt):
        return next(inputs)

    outputs = []
    def output_fn(msg):
        outputs.append(msg)

    cfg = {"agents": {"silver": {"name": "haiku"}}}
    default_cfg = {"agents": {"silver": {"name": "sonnet"}}}
    
    # codex_models_fn=lambda: ["gpt-5.5","o3"]
    result = run_interactive(
        cfg, default_cfg, providers,
        input_fn=input_fn,
        output_fn=output_fn,
        codex_models_fn=lambda: ["gpt-5.5", "o3"]
    )

    assert result["action"] == "oneshot"
    assert result["tier"] == "silver"
    assert result["spec"] == "codex:gpt-5.5"
