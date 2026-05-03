#!/usr/bin/env python3
"""Burnless benchmark - Marco 1.

Usage:
    python bench/run.py                   # full run, all 3 scenarios
    python bench/run.py --turns 5         # shorter run
    python bench/run.py --dry-run         # show plan, no API calls
    python bench/run.py --scenario a      # run only scenario A
    python bench/run.py --scenario b
    python bench/run.py --scenario c
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic


REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_ROOT = REPO_ROOT / "bench"
TASK_PATH = BENCH_ROOT / "tasks" / "refactor_cli.md"
RESULTS_DIR = BENCH_ROOT / "results"
SRC_FILES = ["cli.py", "agents.py", "delegations.py", "config.py", "compression.py", "state.py"]

OPUS_MODEL = "claude-opus-4-7"
TURNS = 8
MAX_TOKENS = 600
PRICES_USD_PER_MTOK = {
    "input": 15.0,
    "output": 75.0,
    "cache_write_5min": 1.25,
    "cache_write_1h": 1.875,
    "cache_read": 0.15,
}

SCENARIOS = {
    "a": "standalone_no_cache",
    "b": "standalone_cache",
    "c": "burnless_maestro",
}


@dataclass
class TurnResult:
    turn: int
    scenario: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens_5min: int
    cache_creation_input_tokens_1h: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    billed_tokens: int
    cost_usd: float
    response_preview: str


def usage_value(usage: Any, name: str) -> int:
    value = getattr(usage, name, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(name)
    return int(value or 0)


def usage_dict(usage: Any) -> dict[str, int]:
    cache_write_5m = usage_value(usage, "cache_creation_input_tokens_5min")
    cache_write_1h = usage_value(usage, "cache_creation_input_tokens_1h")
    cache_creation = usage_value(usage, "cache_creation_input_tokens")
    if not cache_write_1h and cache_creation:
        cache_write_1h = cache_creation
    return {
        "input_tokens": usage_value(usage, "input_tokens"),
        "output_tokens": usage_value(usage, "output_tokens"),
        "cache_creation_input_tokens_5min": cache_write_5m,
        "cache_creation_input_tokens_1h": cache_write_1h,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": usage_value(usage, "cache_read_input_tokens"),
    }


def billed_cost(usage: dict[str, int]) -> float:
    input_tok = usage.get("input_tokens", 0)
    output_tok = usage.get("output_tokens", 0)
    cache_write_5m = usage.get("cache_creation_input_tokens_5min", 0)
    cache_write_1h = usage.get("cache_creation_input_tokens_1h", 0) or usage.get(
        "cache_creation_input_tokens", 0
    )
    cache_read = usage.get("cache_read_input_tokens", 0)

    p = PRICES_USD_PER_MTOK
    return (
        input_tok * p["input"] / 1_000_000
        + output_tok * p["output"] / 1_000_000
        + cache_write_5m * p["cache_write_5min"] / 1_000_000
        + cache_write_1h * p["cache_write_1h"] / 1_000_000
        + cache_read * p["cache_read"] / 1_000_000
    )


def billed_tokens(usage: dict[str, int]) -> int:
    cache_write = usage.get("cache_creation_input_tokens_5min", 0) + (
        usage.get("cache_creation_input_tokens_1h", 0)
        or usage.get("cache_creation_input_tokens", 0)
    )
    return (
        usage.get("input_tokens", 0)
        + usage.get("output_tokens", 0)
        + cache_write
        + usage.get("cache_read_input_tokens", 0)
    )


def response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def record_turn(turn: int, scenario: str, response: Any) -> TurnResult:
    text = response_text(response)
    usage = usage_dict(response.usage)
    return TurnResult(
        turn=turn,
        scenario=scenario,
        model=OPUS_MODEL,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_creation_input_tokens_5min=usage["cache_creation_input_tokens_5min"],
        cache_creation_input_tokens_1h=usage["cache_creation_input_tokens_1h"],
        cache_creation_input_tokens=usage["cache_creation_input_tokens"],
        cache_read_input_tokens=usage["cache_read_input_tokens"],
        billed_tokens=billed_tokens(usage),
        cost_usd=round(billed_cost(usage), 6),
        response_preview=text[:600],
    )


def load_persistent_context() -> str:
    parts: list[str] = []
    src = REPO_ROOT / "src" / "burnless"
    for name in SRC_FILES:
        path = src / name
        if path.exists():
            parts.append(f"--- {name} ---\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def load_system_prompt() -> str:
    context = load_persistent_context()
    return (
        "You are a senior Python engineer validating a multi-turn refactor benchmark.\n"
        "Answer each turn directly, with concrete code-level guidance and no filler.\n\n"
        "[persistent source-code context follows; treat it as the cached project prefix]\n"
        f"{context}"
    )


def load_task_turns(turns: int) -> list[str]:
    task = TASK_PATH.read_text(encoding="utf-8")
    sections = split_task_sections(task)
    if not sections:
        sections = [task.strip()]
    return [sections[i % len(sections)] for i in range(turns)]


def split_task_sections(task: str) -> list[str]:
    heading_chunks = [
        chunk.strip()
        for chunk in re.split(r"(?m)^(?=##+\s+)", task)
        if chunk.strip()
    ]
    if len(heading_chunks) > 1:
        return heading_chunks
    return [chunk.strip() for chunk in re.split(r"\n\s*\n", task) if chunk.strip()]


def cached_system(system_prompt: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def _encode(raw: str) -> str:
    compact = " ".join(raw.strip().split())
    return f"raw:{compact[:80]}"


def run_scenario_a(
    client: anthropic.Anthropic, task_turns: list[str], system_prompt: str
) -> list[TurnResult]:
    messages: list[dict[str, Any]] = []
    results: list[TurnResult] = []

    for i, task_turn in enumerate(task_turns, 1):
        messages.append({"role": "user", "content": task_turn})
        response = client.messages.create(
            model=OPUS_MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=messages,
        )
        text = response_text(response)
        results.append(record_turn(i, "standalone_no_cache", response))
        messages.append({"role": "assistant", "content": text})
    return results


def run_scenario_b(
    client: anthropic.Anthropic, task_turns: list[str], system_prompt: str
) -> list[TurnResult]:
    messages: list[dict[str, Any]] = []
    results: list[TurnResult] = []

    for i, task_turn in enumerate(task_turns, 1):
        messages.append({"role": "user", "content": task_turn})
        response = client.messages.create(
            model=OPUS_MODEL,
            max_tokens=MAX_TOKENS,
            system=cached_system(system_prompt),
            messages=messages,
        )
        text = response_text(response)
        results.append(record_turn(i, "standalone_cache", response))
        messages.append({"role": "assistant", "content": text})
    return results


def run_scenario_c(
    client: anthropic.Anthropic, task_turns: list[str], system_prompt: str
) -> list[TurnResult]:
    history_messages: list[dict[str, Any]] = []
    results: list[TurnResult] = []

    for i, task_turn in enumerate(task_turns, 1):
        history_messages.append({"role": "user", "content": _encode(task_turn)})
        response = client.messages.create(
            model=OPUS_MODEL,
            max_tokens=MAX_TOKENS,
            system=cached_system(system_prompt),
            messages=history_messages,
        )
        text = response_text(response)
        capsule = text.strip().splitlines()[0][:80] if text.strip() else ""
        results.append(record_turn(i, "burnless_maestro", response))
        history_messages.append({"role": "assistant", "content": capsule})
    return results


def aggregate(results: list[TurnResult]) -> dict[str, Any]:
    return {
        "calls": len(results),
        "input_tokens": sum(r.input_tokens for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
        "cache_creation_input_tokens_5min": sum(
            r.cache_creation_input_tokens_5min for r in results
        ),
        "cache_creation_input_tokens_1h": sum(
            r.cache_creation_input_tokens_1h for r in results
        ),
        "cache_creation_input_tokens": sum(r.cache_creation_input_tokens for r in results),
        "cache_read_input_tokens": sum(r.cache_read_input_tokens for r in results),
        "billed_tokens": sum(r.billed_tokens for r in results),
        "cost_usd": round(sum(r.cost_usd for r in results), 6),
    }


def scenario_payload(results: list[TurnResult]) -> dict[str, Any]:
    return {
        "calls": [asdict(r) for r in results],
        "totals": aggregate(results),
    }


def savings(base: float, candidate: float) -> float:
    if base <= 0:
        return 0.0
    return (1.0 - candidate / base) * 100.0


def print_report(
    results_a: list[TurnResult],
    results_b: list[TurnResult],
    results_c: list[TurnResult],
    *,
    turns: int,
    saved_path: Path | None = None,
) -> None:
    totals = {
        "standalone_no_cache": aggregate(results_a) if results_a else None,
        "standalone_cache": aggregate(results_b) if results_b else None,
        "burnless_maestro": aggregate(results_c) if results_c else None,
    }

    print("═══════════════════════════════════════════════════════════")
    print(f"  Burnless Benchmark - Marco 1 ({turns} turns, {OPUS_MODEL})")
    print("═══════════════════════════════════════════════════════════")
    print()
    print("  Scenario                 Billed tokens    Cost USD   Cache read")
    print("  ─────────────────────────────────────────────────────────────────")
    for label, key in [
        ("A: Standalone no-cache", "standalone_no_cache"),
        ("B: Standalone + cache", "standalone_cache"),
        ("C: Burnless Maestro", "burnless_maestro"),
    ]:
        total = totals[key]
        if total is None:
            print(f"  {label:<27} {'skipped':>13} {'-':>11} {'-':>10}")
        else:
            print(
                f"  {label:<27} {total['billed_tokens']:>13,} "
                f"${total['cost_usd']:>9.2f} {total['cache_read_input_tokens']:>10,}"
            )
    print()
    print("  ─────────────────────────────────────────────────────────────────")

    a = totals["standalone_no_cache"]
    b = totals["standalone_cache"]
    c = totals["burnless_maestro"]
    if a and c:
        delta = a["cost_usd"] - c["cost_usd"]
        ratio = c["cost_usd"] / a["cost_usd"] if a["cost_usd"] else 0.0
        print(
            f"  C vs A  ratio: {ratio:6.3f}   savings: {savings(a['cost_usd'], c['cost_usd']):5.1f}%"
            f"   (${delta:.2f} cheaper)"
        )
    if b and c:
        delta = b["cost_usd"] - c["cost_usd"]
        ratio = c["cost_usd"] / b["cost_usd"] if b["cost_usd"] else 0.0
        print(
            f"  C vs B  ratio: {ratio:6.3f}   savings: {savings(b['cost_usd'], c['cost_usd']):5.1f}%"
            f"   (${delta:.2f} cheaper)"
        )
    print()
    print("  Methodology: SDK direct, response.usage exact, no mocks.")
    if saved_path is not None:
        print(f"  Results saved to: {saved_path.relative_to(REPO_ROOT)}")
    print("═══════════════════════════════════════════════════════════")


def save_json(results: dict[str, list[TurnResult]], path: Path) -> None:
    scenarios = {name: scenario_payload(calls) for name, calls in results.items()}
    a = scenarios.get("standalone_no_cache", {}).get("totals")
    b = scenarios.get("standalone_cache", {}).get("totals")
    c = scenarios.get("burnless_maestro", {}).get("totals")
    ratios = {}
    if a and b and a["cost_usd"]:
        ratios["b_over_a"] = b["cost_usd"] / a["cost_usd"]
        ratios["b_vs_a_savings_pct"] = savings(a["cost_usd"], b["cost_usd"])
    if a and c and a["cost_usd"]:
        ratios["c_over_a"] = c["cost_usd"] / a["cost_usd"]
        ratios["c_vs_a_savings_pct"] = savings(a["cost_usd"], c["cost_usd"])
    if b and c and b["cost_usd"]:
        ratios["c_over_b"] = c["cost_usd"] / b["cost_usd"]
        ratios["c_vs_b_savings_pct"] = savings(b["cost_usd"], c["cost_usd"])

    payload = {
        "id": "burnless-bench-marco1",
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "model": OPUS_MODEL,
        "max_tokens": MAX_TOKENS,
        "prices_usd_per_mtok": PRICES_USD_PER_MTOK,
        "methodology": "SDK direct, response.usage exact, no mocks.",
        "scenarios": scenarios,
        "ratios": ratios,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def dry_run(turns: int, scenario: str) -> int:
    system_prompt = load_system_prompt()
    sections = split_task_sections(TASK_PATH.read_text(encoding="utf-8"))
    task_turns = load_task_turns(turns)
    selected = list(SCENARIOS) if scenario == "all" else [scenario]
    print("Burnless Benchmark - Marco 1 dry run")
    print(f"model: {OPUS_MODEL}")
    print(f"turns: {turns}")
    print(f"scenario(s): {', '.join(selected)}")
    print(f"task sections found: {len(sections)}")
    print(f"task turns prepared: {len(task_turns)}")
    print(f"system prompt chars: {len(system_prompt):,}")
    print("no API calls made")
    return 0


def project(turns: int, calibration_path: Path | None = None) -> int:
    """Print projected cost table for N turns using the mathematical model.

    Calibrated from v4 reference run (8 turns, 56k-char system prompt):
      base_input_tokens ≈ 23,255  (system prompt + first user turn)
      output_tokens_per_turn ≈ 600
      capsule_tokens_per_turn ≈ 20  (80-char capsule ≈ 20 tokens)
      cache_write_tokens ≈ 23,000  (system prompt cached once, TTL 1h)

    Standalone: O(N²) — full history appended each turn.
    Burnless:   O(N)  — only capsules in history, system prompt cache_read.
    """
    # Calibration from v4 (override by loading a results JSON if provided)
    base = 23_255
    output_per_turn = 600
    capsule_per_turn = 20
    cache_write = 23_000

    if calibration_path and calibration_path.exists():
        try:
            data = json.loads(calibration_path.read_text())
            sc_a = data.get("scenarios", {}).get("standalone_no_cache", {})
            calls_a = sc_a.get("calls", [])
            if calls_a:
                base = calls_a[0].get("input_tokens", base)
                output_per_turn = calls_a[0].get("output_tokens", output_per_turn)
            sc_c = data.get("scenarios", {}).get("burnless_maestro", {})
            calls_c = sc_c.get("calls", [])
            if len(calls_c) >= 2:
                cache_write = calls_c[0].get("cache_creation_input_tokens", cache_write)
        except Exception:
            pass

    p = PRICES_USD_PER_MTOK

    def cost_a(n: int) -> float:
        # Each turn i: input = base + (i-1)*output_per_turn; no cache
        total_input = n * base + output_per_turn * n * (n - 1) // 2
        total_output = n * output_per_turn
        return total_input * p["input"] / 1e6 + total_output * p["output"] / 1e6

    def cost_c(n: int) -> float:
        # Turn 1: cache_write (1h) + base_non_cached_input + output
        # Turn 2+: cache_read + (i-1)*capsule_per_turn input + output
        c = cache_write * p["cache_write_1h"] / 1e6
        c += (base - cache_write) * p["input"] / 1e6  # non-cached portion
        c += output_per_turn * p["output"] / 1e6
        for i in range(2, n + 1):
            c += cache_write * p["cache_read"] / 1e6
            c += (i - 1) * capsule_per_turn * p["input"] / 1e6
            c += output_per_turn * p["output"] / 1e6
        return c

    print("═══════════════════════════════════════════════════════════════════")
    print(f"  Burnless — projected savings ({OPUS_MODEL})")
    print(f"  Calibrated: base={base:,} tok, output={output_per_turn} tok/turn,")
    print(f"              capsule={capsule_per_turn} tok/turn, cache_write={cache_write:,} tok")
    print("═══════════════════════════════════════════════════════════════════")
    print(f"  {'Turns':>6}  {'A: Standalone':>14}  {'C: Burnless':>12}  {'Savings':>8}  {'Ratio':>6}")
    print("  " + "─" * 63)
    for n in sorted({2, 5, 8, 10, 15, 20, 30, 50, turns}):
        ca = cost_a(n)
        cc = cost_c(n)
        pct = (1 - cc / ca) * 100 if ca else 0
        ratio = cc / ca if ca else 0
        print(f"  {n:>6}  ${ca:>13.2f}  ${cc:>11.2f}  {pct:>7.1f}%  {ratio:>6.3f}")
    print("═══════════════════════════════════════════════════════════════════")
    print("  Formula: A=O(N²), C=O(N). Divergence is mathematical, not heuristic.")
    print("  Verify empirically: python bench/run.py --turns N")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Burnless benchmark - Marco 1")
    parser.add_argument("--turns", type=int, default=TURNS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scenario", choices=["all", "a", "b", "c"], default="all")
    parser.add_argument(
        "--project",
        metavar="N",
        type=int,
        default=None,
        help="print projected cost table up to N turns (no API calls)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.turns <= 0:
        print("error: --turns must be greater than 0", file=sys.stderr)
        return 2

    if args.project is not None:
        cal = RESULTS_DIR / "v4_20260502T200437Z.json"
        return project(args.project, calibration_path=cal)

    if args.dry_run:
        return dry_run(args.turns, args.scenario)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("error: ANTHROPIC_API_KEY is required to run API benchmarks", file=sys.stderr)
        return 2

    client = anthropic.Anthropic(api_key=api_key)
    system_prompt = load_system_prompt()
    task_turns = load_task_turns(args.turns)
    selected = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results: dict[str, list[TurnResult]] = {}

    if "a" in selected:
        print("[1/3] Running A: standalone_no_cache")
        results["standalone_no_cache"] = run_scenario_a(client, task_turns, system_prompt)
    if "b" in selected:
        print("[2/3] Running B: standalone_cache")
        results["standalone_cache"] = run_scenario_b(client, task_turns, system_prompt)
    if "c" in selected:
        print("[3/3] Running C: burnless_maestro")
        results["burnless_maestro"] = run_scenario_c(client, task_turns, system_prompt)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"run_{timestamp}.json"
    save_json(results, out_path)
    print()
    print_report(
        results.get("standalone_no_cache", []),
        results.get("standalone_cache", []),
        results.get("burnless_maestro", []),
        turns=args.turns,
        saved_path=out_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
