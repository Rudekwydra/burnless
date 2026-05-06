"""Cache warm check — measure prefix cache reuse across `claude -p` calls.

Empirical complement to bench/run.py: where run.py hits the SDK directly with
explicit cache_control, this script uses the Claude Code CLI (`claude -p`) and
inspects `usage.cache_read_input_tokens` / `cache_creation.ephemeral_1h_input_tokens`
from --output-format json. Proves cache works on the Claude Code monthly plan
without the SDK or ANTHROPIC_API_KEY.

Output: ~/.burnless/test_data/{timestamp}/cache_warm_check.json (outside the MIT repo).

Usage:
    python bench/cache_warm_check.py --runs 5 --model haiku

The script does NOT consume API credits (uses subscription quota via claude -p).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_RUNS = 5
DEFAULT_MODEL = "haiku"
DEFAULT_PROMPTS = [
    "diga apenas: A",
    "diga apenas: B",
    "diga apenas: C",
    "diga apenas: D",
    "diga apenas: E",
    "diga apenas: F",
    "diga apenas: G",
    "diga apenas: H",
]


def call_claude(prompt: str, model: str) -> dict:
    """Single `claude -p --output-format json` call. Returns parsed JSON dict."""
    proc = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--model", model, prompt],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed (rc={proc.returncode}): {proc.stderr[:500]}")
    return json.loads(proc.stdout.strip().splitlines()[0])


def extract_stats(result: dict) -> dict:
    """Pull the cache-relevant fields from `usage`. Tolerant to missing keys."""
    u = result.get("usage", {}) or {}
    cc = u.get("cache_creation", {}) or {}
    return {
        "input_tokens": u.get("input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "cache_read_input_tokens": u.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0),
        "ephemeral_1h_input_tokens": cc.get("ephemeral_1h_input_tokens", 0),
        "ephemeral_5m_input_tokens": cc.get("ephemeral_5m_input_tokens", 0),
        "duration_ms": result.get("duration_ms", 0),
        "total_cost_usd": result.get("total_cost_usd", 0.0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="number of sequential calls")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="claude model alias (haiku/sonnet/opus)")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between calls")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path.home() / ".burnless" / "test_data" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"runs={args.runs} model={args.model} delay={args.delay}s")
    print(f"output -> {out_dir}/cache_warm_check.json")
    print()

    calls = []
    for i in range(args.runs):
        prompt = DEFAULT_PROMPTS[i % len(DEFAULT_PROMPTS)]
        t0 = time.time()
        try:
            raw = call_claude(prompt, args.model)
            stats = extract_stats(raw)
        except Exception as exc:  # noqa: BLE001 — surface any error cleanly
            print(f"[{i+1}/{args.runs}] FAILED: {exc}", file=sys.stderr)
            return 1
        elapsed = time.time() - t0
        calls.append({"index": i, "prompt": prompt, "elapsed_s": round(elapsed, 2), **stats})
        cr = stats["cache_read_input_tokens"]
        cc = stats["cache_creation_input_tokens"]
        print(f"[{i+1}/{args.runs}] cache_read={cr:>6}  cache_create={cc:>6}  cost=${stats['total_cost_usd']:.4f}  {elapsed:.1f}s")
        if i < args.runs - 1:
            time.sleep(args.delay)

    cold = calls[0]
    warm = calls[1:] if len(calls) > 1 else []
    summary = {
        "session_id": ts,
        "model": args.model,
        "runs": args.runs,
        "cold_call": {"cache_read": cold["cache_read_input_tokens"], "cache_create": cold["cache_creation_input_tokens"]},
        "warm_calls_avg_cache_read": round(sum(c["cache_read_input_tokens"] for c in warm) / len(warm), 1) if warm else 0,
        "warm_calls_avg_cache_create": round(sum(c["cache_creation_input_tokens"] for c in warm) / len(warm), 1) if warm else 0,
        "total_cost_usd": round(sum(c["total_cost_usd"] for c in calls), 4),
    }

    out_file = out_dir / "cache_warm_check.json"
    out_file.write_text(json.dumps({"summary": summary, "calls": calls}, indent=2))
    print()
    print(f"summary: cold cache_read={summary['cold_call']['cache_read']}, "
          f"warm avg cache_read={summary['warm_calls_avg_cache_read']}, "
          f"total cost=${summary['total_cost_usd']}")
    print(f"saved: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
