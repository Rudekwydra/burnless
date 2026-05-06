"""Cache invalidation check — what byte changes break the prefix cache.

Sequence (5 calls):
  1. baseline cold              — first call populates the cache
  2. same prompt warm           — should hit cache
  3. different user prompt      — should still hit (user msg is NOT in cached prefix)
  4. --append-system-prompt set — should miss (prefix changed)
  5. revert (no append again)   — should re-hit original cache (or new cold)

Confirms: only the cached prefix matters; user-message variation is free.

Run order: after `cache_warm_check.py` confirms cache works.
Output: ~/.burnless/test_data/{ts}/cache_invalidation.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def call_claude(prompt: str, model: str, append_system: str | None = None) -> dict:
    cmd = ["claude", "-p", "--output-format", "json", "--model", model]
    if append_system:
        cmd += ["--append-system-prompt", append_system]
    cmd.append(prompt)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed (rc={proc.returncode}): {proc.stderr[:500]}")
    return json.loads(proc.stdout.strip().splitlines()[0])


def extract(r: dict) -> dict:
    u = r.get("usage", {}) or {}
    return {
        "cache_read": u.get("cache_read_input_tokens", 0),
        "cache_create": u.get("cache_creation_input_tokens", 0),
        "input_tokens": u.get("input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "cost_usd": r.get("total_cost_usd", 0.0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--delay", type=float, default=2.0)
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path.home() / ".burnless" / "test_data" / ts
    out.mkdir(parents=True, exist_ok=True)

    cases = [
        ("baseline cold",                          "ping", None),
        ("same prompt warm",                       "ping", None),
        ("different user prompt (still warm)",     "pong", None),
        ("system-prompt change (cold expected)",   "pong", "Extra context: " + "x " * 200),
        ("revert append (warm or new cold)",       "ping", None),
    ]

    print(f"output -> {out}/cache_invalidation.json\n")
    results = []
    for label, prompt, extra in cases:
        s = extract(call_claude(prompt, args.model, extra))
        print(f"  {label:<45} cache_read={s['cache_read']:>6} cache_create={s['cache_create']:>6}  ${s['cost_usd']:.4f}")
        results.append({"label": label, "append_system_set": extra is not None, **s})
        time.sleep(args.delay)

    (out / "cache_invalidation.json").write_text(json.dumps(results, indent=2))
    print(f"\nsaved: {out}/cache_invalidation.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
