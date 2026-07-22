"""Prefix-cache benchmark — honest, per-adapter (M6 Wave B).

Exercises the REAL `burnless ask --prefix-file --cache-key` product surface
(M6 Wave A, commit d7833da) rather than a synthetic shortcut: every call in
this script shells out to the installed `burnless ask` CLI exactly like a
real caller would, letting `--tier` resolve the provider/adapter through the
normal config path.

Procedure (docs/plans/2026-07-21-ask-control-plane-dogfood-handoff.md sec 14):
one cold call with a fresh prefix, then `--runs` warm calls reusing the same
`--prefix-file`/`--cache-key` with a different payload each time. Every call's
usage/cost/latency comes straight from the real `burnless.ask/v1` envelope
(`--output-format json`) or a wall-clock timer this script owns — nothing is
estimated or invented. No `--dry-run` mode: an honest cache benchmark has to
spend real tokens.

NOTE ON `prefix_cache_status`: the design doc's sec 14 text (and the delegation
spec derived from it) assumed an `explain.prefix_cache_status` field. Reading
the actual code path cmd_ask uses for `--explain`
(`pure_ask.render_ask_explain`) shows no such key is ever emitted — only
`explain.capabilities.prefix_cache` (bool). Each provider adapter's own
`explain()` Protocol method *does* build a `prefix_cache_status` string, but
`cmd_ask` never calls that method (dead code as of d7833da). This script
classifies off the real bool that is actually on the wire.

Output: ~/.burnless/test_data/{timestamp}/prefix_cache_bench.json (outside the MIT repo).

Usage:
    python bench/prefix_cache_bench.py --tier gold --runs 3
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_RUNS = 3
CALL_TIMEOUT_S = 150

DEFAULT_PREFIX_TEXT = """[SYNTHETIC BENCHMARK RUBRIC -- NOT A REAL POLICY DOCUMENT]

You are evaluating a fictional short-story submission against the following
rubric. This text exists solely to give bench/prefix_cache_bench.py a stable,
sizeable prefix to exercise prefix-cache reuse; it carries no operational
meaning.

1. Voice and tone (20 points): Does the narrator's voice remain consistent
from the opening line to the closing line? Flag any abrupt shifts in
register, formality, or point of view that are not clearly intentional.
2. Structural coherence (20 points): Does the story have a legible beginning,
middle, and end? Are scene transitions signposted well enough that a reader
never loses track of where or when an event takes place?
3. Character motivation (20 points): Can every major character's key decision
be traced back to an established want, fear, or constraint stated or implied
earlier in the text? Penalize decisions that exist only to serve plot
convenience.
4. Sensory grounding (15 points): Does the prose supply concrete, specific
sensory detail (sound, smell, texture, temperature) rather than relying on
generic description? Award partial credit for detail that is present but
under-used.
5. Dialogue naturalism (15 points): Does dialogue sound like something a
person would actually say under the scene's pressure, or does it read as
expository filler? Flag lines that exist only to inform the reader.
6. Ending resonance (10 points): Does the final beat pay off an earlier
thread -- image, question, or tension -- rather than introducing a new one at
the last moment?

Score each category independently, sum for a total out of 100, and justify
any score below 15/20 or 10/15 with a one-sentence citation of the specific
passage that drove the deduction. Do not average categories together before
scoring; each is graded on its own merits first."""

PAYLOAD_LABELS = [
    "ALPHA", "BETA", "GAMMA", "DELTA", "EPSILON", "ZETA",
    "ETA", "THETA", "IOTA", "KAPPA", "LAMBDA", "MU",
]


def make_payload(label: str) -> str:
    """A short, single-word-answer prompt — keeps output tokens (and cost)
    minimal while still varying per call, per the design doc's "payload
    diferente" requirement."""
    return (
        "Ignore the rubric above for this instruction only: reply with "
        f"exactly the single word {label} and nothing else."
    )


def call_ask(tier: str, prefix_file: str, cache_key: str, prompt: str) -> dict:
    """One real `burnless ask --output-format json --explain` call.

    Returns a dict with the parsed envelope (or None on parse failure),
    the client-measured wall-clock latency, and raw returncode/stderr for
    honest error reporting. Never trusts a self-reported duration field
    instead of measuring — this measures around the subprocess itself.
    """
    cmd = [
        "burnless", "ask",
        "--tier", tier,
        "--prefix-file", prefix_file,
        "--cache-key", cache_key,
        "--output-format", "json",
        "--explain",
        prompt,
    ]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CALL_TIMEOUT_S)
        latency_ms = round((time.time() - t0) * 1000, 1)
    except subprocess.TimeoutExpired as exc:
        latency_ms = round((time.time() - t0) * 1000, 1)
        return {
            "envelope": None,
            "latency_ms": latency_ms,
            "returncode": 1,
            "stderr": f"timed out after {CALL_TIMEOUT_S}s: {exc}",
        }

    envelope = None
    try:
        envelope = json.loads(proc.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    return {
        "envelope": envelope,
        "latency_ms": latency_ms,
        "returncode": proc.returncode,
        "stderr": proc.stderr.strip() if envelope is None else "",
    }


def extract_stats(call: dict) -> dict:
    """Pull the cache-relevant fields straight off the real envelope.
    Tolerant to a missing/failed envelope (records zeros, never guesses)."""
    envelope = call.get("envelope") or {}
    usage = envelope.get("usage") or {}
    cost = envelope.get("cost") or {}
    explain = envelope.get("explain") or {}
    capabilities = explain.get("capabilities") or {}
    return {
        "status": envelope.get("status"),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_tokens", 0),
        "cache_write_tokens": usage.get("cache_write_tokens", 0),
        "cost_usd": cost.get("usd"),
        "server_duration_ms": envelope.get("duration_ms", 0),
        "latency_ms": call["latency_ms"],
        "prefix_cache_capable": capabilities.get("prefix_cache"),
        "provider": envelope.get("provider"),
        "model": envelope.get("model"),
    }


def classify_prefix_cache_result(prefix_cache_capable: bool | None, warm_cache_reads: list[int]) -> str:
    """supported / unsupported / unobservable, per the design doc sec 14.

    `prefix_cache_capable` is the real `explain.capabilities.prefix_cache`
    bool (see module docstring for why it is not `prefix_cache_status`).
    `warm_cache_reads` are the `cache_read_tokens` observed on each warm call
    — classification never assumes a hit, only counts an actually-observed
    one.
    """
    if not prefix_cache_capable:
        return "unsupported"
    if any(n > 0 for n in warm_cache_reads):
        return "supported"
    return "unobservable"


def print_row(tier: str, label: str, stats: dict) -> None:
    print(
        f"{tier:<8} | {label:<8} | input={stats['input_tokens']:<6} | "
        f"cache_read={stats['cache_read_tokens']:<6} | "
        f"cache_write={stats['cache_write_tokens']:<6} | "
        f"latency_ms={stats['latency_ms']:<9}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tier", required=True, choices=["diamond", "gold", "silver", "bronze"],
                     help="tier to benchmark — drives which provider/adapter is exercised via burnless ask --tier")
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                     help="number of warm calls (same prefix, different payload each time)")
    ap.add_argument("--prefix-text", default=DEFAULT_PREFIX_TEXT,
                     help="stable prefix content to benchmark with (written to a temp --prefix-file)")
    args = ap.parse_args()

    if shutil.which("burnless") is None:
        print("prefix_cache_bench: 'burnless' not found on PATH", file=sys.stderr)
        return 1

    if args.runs < 1:
        print("prefix_cache_bench: --runs must be >= 1", file=sys.stderr)
        return 1

    if args.runs > len(PAYLOAD_LABELS) - 1:
        print(
            f"prefix_cache_bench: --runs must be <= {len(PAYLOAD_LABELS) - 1} "
            "(one payload label reserved for the cold call)",
            file=sys.stderr,
        )
        return 1

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path.home() / ".burnless" / "test_data" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "prefix_cache_bench.json"

    cache_key = f"bench-{uuid.uuid4().hex[:12]}"
    print(f"tier={args.tier} runs={args.runs} cache_key={cache_key}")
    print(f"output -> {out_file}")
    print()

    calls = []
    with tempfile.TemporaryDirectory(prefix="burnless-prefix-bench-") as tmpdir:
        prefix_path = Path(tmpdir) / "prefix.txt"
        prefix_path.write_text(args.prefix_text, encoding="utf-8")

        # Cold call — fresh prefix, no cache hit expected yet.
        cold_prompt = make_payload(PAYLOAD_LABELS[0])
        cold_call = call_ask(args.tier, str(prefix_path), cache_key, cold_prompt)
        cold_stats = extract_stats(cold_call)
        calls.append({"phase": "cold", "label": PAYLOAD_LABELS[0], **cold_stats})
        print_row(args.tier, "cold", cold_stats)
        if cold_call["envelope"] is None:
            print(f"prefix_cache_bench: cold call failed: {cold_call['stderr']}", file=sys.stderr)

        # Warm calls — same prefix/cache-key, different payload each time.
        for i in range(args.runs):
            label = PAYLOAD_LABELS[i + 1]
            warm_call = call_ask(args.tier, str(prefix_path), cache_key, make_payload(label))
            warm_stats = extract_stats(warm_call)
            phase = f"warm{i + 1}"
            calls.append({"phase": phase, "label": label, **warm_stats})
            print_row(args.tier, phase, warm_stats)
            if warm_call["envelope"] is None:
                print(f"prefix_cache_bench: {phase} call failed: {warm_call['stderr']}", file=sys.stderr)

    prefix_cache_capable = cold_stats.get("prefix_cache_capable")
    warm_cache_reads = [c["cache_read_tokens"] for c in calls if c["phase"] != "cold"]
    result = classify_prefix_cache_result(prefix_cache_capable, warm_cache_reads)

    summary = {
        "session_id": ts,
        "tier": args.tier,
        "provider": cold_stats.get("provider"),
        "model": cold_stats.get("model"),
        "runs": args.runs,
        "cache_key": cache_key,
        "prefix_cache_capable": prefix_cache_capable,
        "cold_call": {
            "cache_read_tokens": cold_stats["cache_read_tokens"],
            "cache_write_tokens": cold_stats["cache_write_tokens"],
        },
        "warm_calls_any_cache_read": any(n > 0 for n in warm_cache_reads),
        "result": result,
    }

    out_file.write_text(json.dumps({"summary": summary, "calls": calls}, indent=2))
    print()
    print(f"saved: {out_file}")
    print(f"RESULT: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
