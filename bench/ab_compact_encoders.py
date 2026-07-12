#!/usr/bin/env python3
"""A/B benchmark: compact encoders (gemma4 local × haiku API) on golden harness."""

import sys
sys.path.insert(0, "/Users/roberto/antigravity/burnless/tests")

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

import yaml

import test_memory_golden as g
from burnless.epochs_v2 import living_rewriter
from burnless import recovery


ENCODERS = {
    "fake": ("none", "none"),
    "gemma": ("ollama-local", "hf.co/unsloth/gemma-4-E4B-it-qat-GGUF:UD-Q4_K_XL"),
    "haiku": ("anthropic", "haiku"),
}


def parse_encoder_arg(spec):
    """Parse encoder spec: NAME or NAME=PROVIDER:MODEL."""
    if "=" not in spec:
        if spec not in ENCODERS:
            raise ValueError(f"unknown encoder: {spec}")
        return spec, ENCODERS[spec]
    name, rest = spec.split("=", 1)
    provider, model = rest.split(":", 1)
    return name, (provider, model)


def make_wrapper_rewriter(rewriter_fn):
    """Wrap rewriter to measure latency and count failures."""
    calls = []
    failures = 0

    def wrapper(prompt):
        t0 = time.monotonic()
        result = rewriter_fn(prompt)
        elapsed = time.monotonic() - t0
        calls.append(elapsed)
        nonlocal failures
        if result is None:
            failures += 1
        return result

    wrapper.calls = calls
    wrapper.failures = failures
    return wrapper


def score_restore(scenario, run, encoder_name):
    """Extract fidelity and latency scores from final restore."""
    if not run.restores:
        return {"error": "no restores produced"}

    final_restore = run.restores[-1]
    ctx = final_restore["hookSpecificOutput"]["additionalContext"]
    meta = final_restore["recovery"]

    # must_remember: hits/total
    must_remember_hits = 0
    must_remember_lost = []
    for needle in scenario.get("must_remember", []):
        if needle in ctx:
            must_remember_hits += 1
        else:
            must_remember_lost.append(needle)
    must_remember_total = len(scenario.get("must_remember", []))

    # must_forget: leaks (should be 0)
    must_forget_leaks = 0
    must_forget_leaked = []
    for needle in scenario.get("must_forget", []):
        if needle in ctx:
            must_forget_leaks += 1
            must_forget_leaked.append(needle)
    must_forget_total = len(scenario.get("must_forget", []))

    # must_reach: in payload or checkpoint
    must_reach_found = 0
    must_reach_missing = []
    checkpoint = recovery.read_checkpoint(run.root, "claude", meta["old_session"])
    checkpoint_md = (checkpoint or {}).get("living_md") or ""
    for needle in scenario.get("must_reach", []):
        if needle in ctx or needle in checkpoint_md:
            must_reach_found += 1
        else:
            must_reach_missing.append(needle)
    must_reach_total = len(scenario.get("must_reach", []))

    # last_exchange_verbatim
    last_turn = run.last_turns[-1]
    last_exchange_verbatim = (last_turn["user"] in ctx) and (last_turn["assistant"] in ctx)

    # budget_ok
    budget = g._effective_budget_tokens(scenario)
    budget_ok = len(ctx) <= budget * 4

    result = {
        "encoder": encoder_name,
        "must_remember_hits": must_remember_hits,
        "must_remember_total": must_remember_total,
        "must_remember_lost": must_remember_lost,
        "must_forget_leaks": must_forget_leaks,
        "must_forget_total": must_forget_total,
        "must_forget_leaked": must_forget_leaked,
        "must_reach_found": must_reach_found,
        "must_reach_total": must_reach_total,
        "must_reach_missing": must_reach_missing,
        "last_exchange_verbatim": last_exchange_verbatim,
        "budget_ok": budget_ok,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="A/B benchmark: compact encoders on golden harness")
    parser.add_argument(
        "--encoder",
        action="append",
        dest="encoders",
        default=[],
        help="encoder spec: NAME or NAME=PROVIDER:MODEL (default: fake); repetible"
    )
    parser.add_argument(
        "--fixtures",
        action="append",
        dest="fixture_globs",
        default=[],
        help="fixture glob pattern (default: all *.yaml in golden/); repetible"
    )
    parser.add_argument(
        "--out",
        default="/Users/roberto/antigravity/burnless/bench/results/ab_compact_encoders.json",
        help="output JSON path"
    )
    args = parser.parse_args()

    # Default encoder
    if not args.encoders:
        args.encoders = ["fake"]

    # Default fixtures
    if not args.fixture_globs:
        golden_dir = Path("/Users/roberto/antigravity/burnless/tests/fixtures/golden")
        fixture_paths = sorted(golden_dir.glob("*.yaml"))
        fixture_paths = [p for p in fixture_paths if p.name != "rewriter_outage.yaml"]
    else:
        fixture_paths = []
        for glob_pattern in args.fixture_globs:
            fixture_paths.extend(Path("/Users/roberto/antigravity/burnless/tests/fixtures/golden").glob(glob_pattern))
        fixture_paths = sorted(set(fixture_paths))

    # Parse encoders
    encoder_specs = {}
    for enc_arg in args.encoders:
        name, (provider, model) = parse_encoder_arg(enc_arg)
        encoder_specs[name] = (provider, model)

    results = []
    any_success = False

    for fixture_path in fixture_paths:
        scenario = g._load(fixture_path)
        fixture_name = fixture_path.stem

        for encoder_name, (provider, model) in encoder_specs.items():
            result = {
                "fixture": fixture_name,
                "encoder": encoder_name,
            }

            try:
                if encoder_name == "fake":
                    # Use fake_rewriter directly (no temp config needed)
                    rw = g.fake_rewriter
                    wrapped_rw = make_wrapper_rewriter(rw)
                    with tempfile.TemporaryDirectory() as tmp_proj:
                        tmp_proj_path = Path(tmp_proj)
                        run = g.GoldenRun(scenario, tmp_proj_path, rewriter=wrapped_rw)
                        run.run()
                else:
                    # Create temp dir with config
                    with tempfile.TemporaryDirectory() as tmp_proj:
                        tmp_proj_path = Path(tmp_proj)
                        (tmp_proj_path / ".burnless").mkdir(parents=True, exist_ok=True)
                        config_yaml = f"""
encoder:
  provider: {provider}
  model: {model}
"""
                        (tmp_proj_path / ".burnless" / "config.yaml").write_text(config_yaml.strip(), encoding="utf-8")
                        rw = living_rewriter(str(tmp_proj_path))
                        wrapped_rw = make_wrapper_rewriter(rw)
                        tmp_proj_for_run = Path(tempfile.mkdtemp())
                        try:
                            run = g.GoldenRun(scenario, tmp_proj_for_run, rewriter=wrapped_rw)
                            run.run()
                        finally:
                            import shutil
                            shutil.rmtree(tmp_proj_for_run, ignore_errors=True)

                # Score the run
                score = score_restore(scenario, run, encoder_name)
                result.update(score)

                # Latency stats
                if hasattr(wrapped_rw, "calls") and wrapped_rw.calls:
                    calls = wrapped_rw.calls
                    result["compact_calls"] = len(calls)
                    result["failures"] = wrapped_rw.failures
                    result["mean_s"] = round(statistics.mean(calls), 3) if calls else 0.0
                    if len(calls) > 1:
                        result["p95_s"] = round(sorted(calls)[int(len(calls) * 0.95)], 3)
                    else:
                        result["p95_s"] = round(calls[0], 3) if calls else 0.0
                    result["max_s"] = round(max(calls), 3) if calls else 0.0
                else:
                    result["compact_calls"] = 0
                    result["failures"] = 0
                    result["mean_s"] = 0.0
                    result["p95_s"] = 0.0
                    result["max_s"] = 0.0

                any_success = True

            except Exception as e:
                result["error"] = str(e)

            results.append(result)

    # Write JSON
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"results": results}, f, indent=2)

    # Print markdown table
    print("| Fixture | Encoder | Remember % | Forget Leaks | Last Verbatim | Budget OK | Calls | Failures | Mean (s) | P95 (s) | Max (s) |")
    print("|---------|---------|------------|--------------|---------------|-----------|-------|----------|----------|---------|---------|")
    for r in results:
        if "error" in r:
            print(f"| {r['fixture']} | {r['encoder']} | ERROR: {r['error']} |")
        else:
            remember_pct = f"{round(100*r['must_remember_hits']/r['must_remember_total']) if r['must_remember_total'] else 'N/A'}%"
            forget_leaks = r["must_forget_leaks"]
            last_verb = "✓" if r["last_exchange_verbatim"] else "✗"
            budget_ok = "✓" if r["budget_ok"] else "✗"
            calls = r.get("compact_calls", 0)
            failures = r.get("failures", 0)
            mean_s = r.get("mean_s", 0.0)
            p95_s = r.get("p95_s", 0.0)
            max_s = r.get("max_s", 0.0)
            print(f"| {r['fixture']} | {r['encoder']} | {remember_pct} | {forget_leaks} | {last_verb} | {budget_ok} | {calls} | {failures} | {mean_s} | {p95_s} | {max_s} |")

    print(f"\nResults written to: {output_path}")
    return 0 if any_success else 1


if __name__ == "__main__":
    sys.exit(main())
