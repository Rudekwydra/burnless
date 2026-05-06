"""Cache TTL persistence — does cache survive longer gaps (lunch scenario)?

Sequence: warmup, then 5 calls separated by configurable gaps (default 0/30/60/120/300s).
Validates the keep-alive feature roadmap: how long can a user be idle before cache decays?

Run order: optional, after cache_warm_check confirms baseline.
Estimated runtime: ~9 minutes default (most of it sleep).
Output: ~/.burnless/test_data/{ts}/cache_persistence.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def call_claude(prompt: str, model: str) -> dict:
    proc = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--model", model, prompt],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed: {proc.stderr[:500]}")
    return json.loads(proc.stdout.strip().splitlines()[0])


def extract(r: dict) -> dict:
    u = r.get("usage", {}) or {}
    return {
        "cache_read": u.get("cache_read_input_tokens", 0),
        "cache_create": u.get("cache_creation_input_tokens", 0),
        "cost_usd": r.get("total_cost_usd", 0.0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--gaps", default="0,30,60,120,300", help="comma-separated seconds between calls")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path.home() / ".burnless" / "test_data" / ts
    out.mkdir(parents=True, exist_ok=True)

    gaps = [int(g) for g in args.gaps.split(",")]
    print("warming cache (cold call)...")
    extract(call_claude("warmup", args.model))

    results = []
    for gap in gaps:
        if gap > 0:
            print(f"  sleeping {gap}s...")
            time.sleep(gap)
        s = extract(call_claude(f"after {gap}s gap, say ok", args.model))
        print(f"  gap={gap:>4}s  cache_read={s['cache_read']:>6}  cache_create={s['cache_create']:>6}  ${s['cost_usd']:.4f}")
        results.append({"gap_seconds": gap, **s})

    (out / "cache_persistence.json").write_text(json.dumps(results, indent=2))
    print(f"\nsaved: {out}/cache_persistence.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
