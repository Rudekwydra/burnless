"""Tier routing savings — Haiku/Sonnet/Opus mix vs all-Sonnet.

Runs a small batch of tasks twice:
  - Tier-routed: bronze→haiku, silver→sonnet, gold→opus
  - Single-tier: every task with --single-tier-model (default: sonnet)

Compares total cost. Validates the tier-mix multiplier on top of capsules+cache.

Run order: after replay_vs_capsule confirms the curve.
Estimated runtime: ~2 minutes.
Output: ~/.burnless/test_data/{ts}/tier_routing.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


TASKS = [
    {"tier": "bronze", "tier_model": "haiku",
     "prompt": "Summarize in one sentence: 'The quick brown fox jumps over the lazy dog while a thunderstorm builds in the west.'"},
    {"tier": "bronze", "tier_model": "haiku",
     "prompt": "Classify as positive, negative, or neutral: 'It was alright I guess, nothing special.'"},
    {"tier": "bronze", "tier_model": "haiku",
     "prompt": "Extract any year mentioned: 'The treaty was signed in 1648 after long negotiations.'"},
    {"tier": "silver", "tier_model": "sonnet",
     "prompt": "Write a Python function that takes a list of integers and returns the median, handling empty lists with None. Just code, no commentary."},
    {"tier": "silver", "tier_model": "sonnet",
     "prompt": "Refactor this for readability: `x=[i for i in range(10) if i%2==0 and i>2]`. Just the code."},
    {"tier": "gold", "tier_model": "opus",
     "prompt": "In 3 sentences, what is the key trade-off between SQL and NoSQL for a high-write event log?"},
]


def call_claude(prompt: str, model: str) -> dict:
    proc = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--model", model, prompt],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed (model={model}): {proc.stderr[:500]}")
    return json.loads(proc.stdout.strip().splitlines()[0])


def run_mode(tasks: list[dict], use_routing: bool, fallback_model: str) -> list[dict]:
    out = []
    for t in tasks:
        model = t["tier_model"] if use_routing else fallback_model
        r = call_claude(t["prompt"], model)
        u = r.get("usage", {}) or {}
        out.append({
            "tier": t["tier"], "model_used": model,
            "input_tokens": u.get("input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
            "cache_read": u.get("cache_read_input_tokens", 0),
            "cost_usd": r.get("total_cost_usd", 0.0),
        })
        time.sleep(1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--single-tier-model", default="sonnet")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path.home() / ".burnless" / "test_data" / ts
    out.mkdir(parents=True, exist_ok=True)

    print("=== Mode A: Tier-routed (bronze->haiku, silver->sonnet, gold->opus) ===")
    a = run_mode(TASKS, use_routing=True, fallback_model=args.single_tier_model)
    cost_a = sum(t["cost_usd"] for t in a)
    for t in a:
        print(f"  {t['tier']:<7} {t['model_used']:<8} in={t['input_tokens']:>5} out={t['output_tokens']:>4} ${t['cost_usd']:.4f}")
    print(f"  total: ${cost_a:.4f}")

    print(f"\n=== Mode B: Single-tier (all {args.single_tier_model}) ===")
    b = run_mode(TASKS, use_routing=False, fallback_model=args.single_tier_model)
    cost_b = sum(t["cost_usd"] for t in b)
    for t in b:
        print(f"  {t['tier']:<7} {t['model_used']:<8} in={t['input_tokens']:>5} out={t['output_tokens']:>4} ${t['cost_usd']:.4f}")
    print(f"  total: ${cost_b:.4f}")

    ratio = (cost_b / cost_a) if cost_a > 0 else float("inf")
    print(f"\nratio: {ratio:.2f}x more expensive without tier routing")

    (out / "tier_routing.json").write_text(json.dumps({
        "tier_routed": a, "single_tier": b,
        "total_routed": cost_a, "total_single": cost_b, "ratio": ratio,
    }, indent=2))
    print(f"saved: {out}/tier_routing.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
