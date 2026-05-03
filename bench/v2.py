"""Burnless Bench v2 — Monte Carlo cost simulation across N-turn agent loops.

Implements the formula derived in MATH.md §4. No API calls; reproducible from
prices and per-scenario parameters alone. Run --simulate (default) on any laptop
without an API key. Real-API validation lives in --real (advanced; consumes
provider credits).

Usage:
    python bench/v2.py                          # 30 runs × 100 turns × 4 scenarios
    python bench/v2.py --runs 100 --turns 50    # custom
    python bench/v2.py --seed 42                # reproducible
    python bench/v2.py --scenarios A1,Z         # subset

Output:
    Console table with p10/p50/p90/mean per scenario in $.
    JSON dump in bench/results/v2_simulated_<ts>.json with full per-run costs.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "bench" / "results"

# MATH.md §6 — Anthropic May 2026 reference prices. USD per million tokens.
PRICES_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "opus":   {"in": 15.00, "cw": 18.75, "cr": 1.50, "out": 75.00},
    "sonnet": {"in":  3.00, "cw":  3.75, "cr": 0.30, "out": 15.00},
    "haiku":  {"in":  0.80, "cw":  1.00, "cr": 0.08, "out":  4.00},
}

CAPSULE_TOKENS_FLOOR = 5  # MATH.md §3 — capsules never below 5 tokens

DEFAULT_PREFIX_TOKENS = 23_000          # system prompt + tools (matches v1 calibration)
DEFAULT_USER_RANGE = (2_000, 10_000)    # U_k ~ Uniform
DEFAULT_OUTPUT_RANGE = (200, 1_500)     # O_k ~ Uniform
DEFAULT_ALPHA_RANGE = (0.20, 0.30)      # capsule = α × (U+O), so 70-80% economy
DEFAULT_BRAIN_OUTPUT = 200              # orchestration tokens emitted by Brain per turn

# Z worker mix — MATH.md §5.Z midpoints of stated ranges.
DEFAULT_WORKER_MIX = {"opus": 0.07, "sonnet": 0.30, "haiku": 0.63}


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

@dataclass
class Turn:
    user_in: int
    output: int
    alpha: float


def sample_session(turns: int, rng: random.Random) -> list[Turn]:
    return [
        Turn(
            user_in=rng.randint(*DEFAULT_USER_RANGE),
            output=rng.randint(*DEFAULT_OUTPUT_RANGE),
            alpha=rng.uniform(*DEFAULT_ALPHA_RANGE),
        )
        for _ in range(turns)
    ]


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def cost_call(model: str, *, fresh_in: int, cache_read: int, cache_write: int, output: int) -> float:
    p = PRICES_USD_PER_MTOK[model]
    return (
        fresh_in   * p["in"]
        + cache_read  * p["cr"]
        + cache_write * p["cw"]
        + output      * p["out"]
    ) / 1_000_000


# ---------------------------------------------------------------------------
# Scenarios — direct implementations of MATH.md §5
# ---------------------------------------------------------------------------

def cost_standalone(session: list[Turn], prefix: int, model: str) -> float:
    """A1 / A2 — single-model loop, prefix cached, full history replayed.

    Turn k input: prefix (cache_read) + sum_{j<k}(U_j + O_j) (fresh) + U_k (fresh).
    Turn 0 writes the cache; subsequent turns read it.
    """
    total = 0.0
    history = 0  # accumulated U_j + O_j from prior turns
    for k, t in enumerate(session):
        cw = prefix if k == 0 else 0
        cr = 0      if k == 0 else prefix
        fresh = history + t.user_in
        total += cost_call(model, fresh_in=fresh, cache_read=cr, cache_write=cw, output=t.output)
        history += t.user_in + t.output
    return total


def cost_freepick(session: list[Turn], prefix: int, mix: list[str], rng: random.Random) -> float:
    """B — developer picks a model per turn; cache is per (model, prefix).

    Each model's prefix cache is cold the first time it sees the prefix and warm
    afterward (we do not model TTL expiration; this is an OPTIMISTIC bound for B).
    Real-world B would be even worse with cold restarts.
    """
    total = 0.0
    history = 0
    cache_warm: dict[str, bool] = {}
    for t in session:
        m = rng.choice(mix)
        if cache_warm.get(m):
            cw, cr = 0, prefix
        else:
            cw, cr = prefix, 0
            cache_warm[m] = True
        fresh = history + t.user_in
        total += cost_call(m, fresh_in=fresh, cache_read=cr, cache_write=cw, output=t.output)
        history += t.user_in + t.output
    return total


def cost_burnless(
    session: list[Turn],
    prefix: int,
    rng: random.Random,
    brain: str = "sonnet",
    worker_mix: dict[str, float] | None = None,
    brain_output: int = DEFAULT_BRAIN_OUTPUT,
) -> float:
    """Z — Sonnet Brain + capsule history + tier workers, shared prefix cache.

    Brain at turn k:    prefix (cw on k=0, cr after) + sum capsules + U_k fresh
                        produces brain_output tokens (orchestration).
    Worker per turn:    one delegation, model picked from worker_mix.
                        Worker shares the byte-identical prefix; treats it as
                        cache_read after the model's first delegation.
    """
    if worker_mix is None:
        worker_mix = DEFAULT_WORKER_MIX

    workers = list(worker_mix.keys())
    weights = list(worker_mix.values())

    total = 0.0
    capsule_history = 0
    worker_warm: dict[str, bool] = {}

    for k, t in enumerate(session):
        # Brain call
        brain_cw = prefix if k == 0 else 0
        brain_cr = 0      if k == 0 else prefix
        brain_fresh = capsule_history + t.user_in
        total += cost_call(brain,
                           fresh_in=brain_fresh,
                           cache_read=brain_cr,
                           cache_write=brain_cw,
                           output=brain_output)

        # Worker delegation (one per turn — conservative; real Z amortizes
        # several turns into one worker call sometimes).
        wm = rng.choices(workers, weights=weights, k=1)[0]
        if worker_warm.get(wm):
            wcw, wcr = 0, prefix
        else:
            wcw, wcr = prefix, 0
            worker_warm[wm] = True
        # Worker input: focused task + relevant capsules (≈ 3 capsules typical).
        worker_fresh = t.user_in + 3 * max(int(t.alpha * (t.user_in + t.output)), CAPSULE_TOKENS_FLOOR)
        total += cost_call(wm,
                           fresh_in=worker_fresh,
                           cache_read=wcr,
                           cache_write=wcw,
                           output=t.output)

        # Capsule appended to brain history for next turn
        capsule_history += max(int(t.alpha * (t.user_in + t.output)), CAPSULE_TOKENS_FLOOR)

    return total


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass
class ScenarioSpec:
    id: str
    name: str
    description: str


SCENARIOS = [
    ScenarioSpec("A1", "Pure Opus 100",
                 "Opus every turn. Cached prefix, full history. Best capability, worst cost."),
    ScenarioSpec("A2", "Pure Sonnet 100",
                 "Sonnet every turn. Cached prefix, full history."),
    ScenarioSpec("B",  "Free-pick (Opus/Sonnet)",
                 "Random per-turn pick between Opus and Sonnet. Cache thrashes on switches."),
    ScenarioSpec("Z",  "Burnless (Sonnet Brain + tier workers)",
                 "Sonnet Brain + capsules + workers Opus/Sonnet/Haiku per worker_mix."),
]


def run_scenario(sid: str, session: list[Turn], prefix: int, rng: random.Random) -> float:
    if sid == "A1":
        return cost_standalone(session, prefix, "opus")
    if sid == "A2":
        return cost_standalone(session, prefix, "sonnet")
    if sid == "B":
        return cost_freepick(session, prefix, mix=["opus", "sonnet"], rng=rng)
    if sid == "Z":
        return cost_burnless(session, prefix, rng=rng)
    raise ValueError(f"unknown scenario: {sid}")


def aggregate(costs: list[float]) -> dict:
    sorted_costs = sorted(costs)
    n = len(sorted_costs)
    def pct(p: float) -> float:
        idx = max(0, min(n - 1, int(p * n)))
        return sorted_costs[idx]
    return {
        "n": n,
        "mean":   round(statistics.mean(costs), 4),
        "median": round(statistics.median(costs), 4),
        "p10":    round(pct(0.10), 4),
        "p90":    round(pct(0.90), 4),
        "stdev":  round(statistics.stdev(costs), 4) if n > 1 else 0.0,
        "min":    round(min(costs), 4),
        "max":    round(max(costs), 4),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(results: dict[str, dict], turns: int, prefix: int, runs: int, seed: int | None) -> None:
    line = "═" * 78
    print()
    print(line)
    print(f"  Burnless Bench v2 — {runs} Monte Carlo runs × {turns} turns")
    print(f"  Prefix tokens: {prefix:,}    Seed: {seed if seed is not None else 'random'}")
    print(line)
    print()
    print(f"  {'Scenario':<40} {'p10':>8} {'p50':>8} {'p90':>8} {'mean':>8}")
    print(f"  {'-'*40} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for sid, agg in results.items():
        spec = next(s for s in SCENARIOS if s.id == sid)
        label = f"{sid}  {spec.name}"
        print(f"  {label:<40} ${agg['p10']:>7.2f} ${agg['median']:>7.2f} ${agg['p90']:>7.2f} ${agg['mean']:>7.2f}")
    print()

    a1 = results.get("A1", {}).get("mean", 0)
    if a1:
        print("  Savings vs A1 (Pure Opus baseline):")
        for sid, agg in results.items():
            if sid == "A1":
                continue
            spec = next(s for s in SCENARIOS if s.id == sid)
            saving = (1 - agg["mean"] / a1) * 100
            ratio = a1 / agg["mean"] if agg["mean"] else float("inf")
            print(f"    {sid:<3} {spec.name:<40}  −{saving:>5.1f}%   ({ratio:>5.1f}× cheaper)")
    print()
    print(line)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Burnless Monte Carlo cost simulation")
    p.add_argument("--runs", type=int, default=30, help="Monte Carlo runs per scenario (default: 30)")
    p.add_argument("--turns", type=int, default=100, help="turns per session (default: 100)")
    p.add_argument("--prefix", type=int, default=DEFAULT_PREFIX_TOKENS,
                   help=f"persistent prefix tokens (default: {DEFAULT_PREFIX_TOKENS})")
    p.add_argument("--seed", type=int, default=None, help="random seed (default: nondeterministic)")
    p.add_argument("--scenarios", type=str, default="A1,A2,B,Z",
                   help="comma-separated scenario IDs (default: A1,A2,B,Z)")
    p.add_argument("--output", type=str, default=None,
                   help="output JSON path (default: bench/results/v2_simulated_<ts>.json)")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    selected = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    valid_ids = {s.id for s in SCENARIOS}
    for sid in selected:
        if sid not in valid_ids:
            print(f"error: unknown scenario {sid}; valid: {sorted(valid_ids)}", file=sys.stderr)
            return 2

    rng = random.Random(args.seed)

    results: dict[str, dict] = {}
    raw: dict[str, list[float]] = {}
    for sid in selected:
        costs = []
        for _ in range(args.runs):
            session = sample_session(args.turns, rng)
            costs.append(run_scenario(sid, session, args.prefix, rng))
        results[sid] = aggregate(costs)
        raw[sid] = [round(c, 6) for c in costs]

    print_report(results, args.turns, args.prefix, args.runs, args.seed)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.output) if args.output else (RESULTS_DIR / f"v2_simulated_{timestamp}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": "burnless-bench-v2-simulated",
        "timestamp": timestamp,
        "params": {
            "runs": args.runs,
            "turns": args.turns,
            "prefix_tokens": args.prefix,
            "seed": args.seed,
            "scenarios": selected,
            "user_input_range": list(DEFAULT_USER_RANGE),
            "output_range": list(DEFAULT_OUTPUT_RANGE),
            "alpha_range": list(DEFAULT_ALPHA_RANGE),
            "brain_output": DEFAULT_BRAIN_OUTPUT,
            "worker_mix": DEFAULT_WORKER_MIX,
        },
        "prices_usd_per_mtok": PRICES_USD_PER_MTOK,
        "results": results,
        "raw_costs_usd": raw,
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"  Results saved to: {out_path.relative_to(REPO_ROOT)}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
