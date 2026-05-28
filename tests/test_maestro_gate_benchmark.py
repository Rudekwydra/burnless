import importlib.util
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).parent / "benchmarks" / "maestro_gate.py"
    spec = importlib.util.spec_from_file_location("maestro_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_current_variant_uses_system_prompt_and_no_tools():
    gate = _load_module()
    cmd = gate.build_variant_command('{"intent":"x"}', "current", "claude-haiku-4-5-20251001")

    assert "--system-prompt" in cmd
    assert "--tools" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--append-system-prompt" not in cmd
    assert "--disallowedTools" not in cmd


def test_summary_computes_variant_ratios():
    gate = _load_module()
    results = {
        "variants": ["current", "append_disallowed"],
        "runs": [
            {
                "variant": "current",
                "ok": True,
                "cost_usd": 1.0,
                "input_tokens": 100,
                "output_tokens": 10,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "wall_s": 1.0,
            },
            {
                "variant": "append_disallowed",
                "ok": True,
                "cost_usd": 3.0,
                "input_tokens": 200,
                "output_tokens": 10,
                "cache_creation_input_tokens": 1000,
                "cache_read_input_tokens": 0,
                "wall_s": 2.0,
            },
        ],
    }

    summary = gate.summarize(results)

    assert summary["all_correct"] is True
    assert summary["current_correct"] is True
    assert summary["variants"]["append_disallowed"]["cost_ratio_vs_current"] == 3.0


def test_summary_allows_comparison_variant_failure():
    gate = _load_module()
    results = {
        "variants": ["current", "append_disallowed"],
        "runs": [
            {
                "variant": "current",
                "ok": True,
                "cost_usd": 1.0,
                "input_tokens": 100,
                "output_tokens": 10,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "wall_s": 1.0,
            },
            {
                "variant": "append_disallowed",
                "ok": False,
                "cost_usd": 3.0,
                "input_tokens": 200,
                "output_tokens": 10,
                "cache_creation_input_tokens": 1000,
                "cache_read_input_tokens": 0,
                "wall_s": 2.0,
            },
        ],
    }

    summary = gate.summarize(results)

    assert summary["all_correct"] is False
    assert summary["current_correct"] is True
