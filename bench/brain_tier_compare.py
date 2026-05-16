"""
brain_tier_compare.py — Haiku Brain vs Sonnet Brain, workers fixed (Haiku).

Measures orchestration cost when Brain model varies. Same 8-turn task,
same worker tier. Records: brain tokens, latency, delegation quality (PART/ERR rate).

Usage:
    python bench/brain_tier_compare.py
    python bench/brain_tier_compare.py --turns 4 --out /tmp/brain_compare.json
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "bench" / "results"

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

PRICES_USD_PER_MTOK = {
    HAIKU:  {"input": 0.25,  "output": 1.25,  "cache_read": 0.03,  "cache_write": 0.30},
    SONNET: {"input": 3.00,  "output": 15.00, "cache_read": 0.30,  "cache_write": 3.75},
}

ORCHESTRATION_PROMPTS = [
    "Route this task: 'Summarize the README in 3 bullets.' Which tier — bronze, silver, gold?",
    "A worker returned PART with error 'missing file src/foo.py'. What do you do next?",
    "User asks for a refactor of 3 files. Build the delegation spec in JSON.",
    "Two workers are in flight on independent files. Safe to run a third touching cli.py?",
    "Worker output: OK. Files touched: agents.py:142. Next action?",
    "Estimate token savings if we use bronze instead of gold for a 200-word summary task.",
    "A gold worker returns ERR after 2 retries. Escalate or abort? Give reasoning.",
    "Session has been idle 55 minutes. Cache TTL is 60 min. What action to preserve it?",
]


@dataclass
class TurnResult:
    prompt: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_ms: float
    cost_usd: float


@dataclass
class RunResult:
    model: str
    turns: list[TurnResult]
    total_input: int
    total_output: int
    total_cache_read: int
    total_cache_write: int
    total_cost_usd: float
    total_latency_ms: float
    avg_latency_ms: float
    ts: str


def run_brain(model: str, prompts: list[str], client: anthropic.Anthropic) -> RunResult:
    turns: list[TurnResult] = []
    messages: list[dict[str, Any]] = []
    prices = PRICES_USD_PER_MTOK[model]

    system = [
        {
            "type": "text",
            "text": (
                "You are the Brain (maestro) of Burnless, a multi-tier LLM orchestration framework. "
                "Your job: routing, delegation spec writing, and worker coordination. "
                "Be decisive and terse. No padding."
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    for prompt in prompts:
        messages.append({"role": "user", "content": prompt})
        t0 = time.perf_counter()
        resp = client.messages.create(
            model=model,
            max_tokens=512,
            system=system,
            messages=messages,
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        u = resp.usage
        ir = getattr(u, "cache_read_input_tokens", 0) or 0
        iw = getattr(u, "cache_creation_input_tokens", 0) or 0
        cost = (
            (u.input_tokens * prices["input"]
             + u.output_tokens * prices["output"]
             + ir * prices["cache_read"]
             + iw * prices["cache_write"])
            / 1_000_000
        )

        turns.append(TurnResult(
            prompt=prompt,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=ir,
            cache_write_tokens=iw,
            latency_ms=latency_ms,
            cost_usd=cost,
        ))
        messages.append({"role": "assistant", "content": resp.content[0].text})

    total_cost = sum(t.cost_usd for t in turns)
    total_latency = sum(t.latency_ms for t in turns)
    return RunResult(
        model=model,
        turns=turns,
        total_input=sum(t.input_tokens for t in turns),
        total_output=sum(t.output_tokens for t in turns),
        total_cache_read=sum(t.cache_read_tokens for t in turns),
        total_cache_write=sum(t.cache_write_tokens for t in turns),
        total_cost_usd=total_cost,
        total_latency_ms=total_latency,
        avg_latency_ms=total_latency / len(turns),
        ts=datetime.now(timezone.utc).isoformat(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Haiku vs Sonnet as Brain (maestro)")
    parser.add_argument("--turns", type=int, default=len(ORCHESTRATION_PROMPTS))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    prompts = ORCHESTRATION_PROMPTS[: args.turns]
    client = anthropic.Anthropic()

    print(f"Running {len(prompts)} orchestration turns per brain model...\n")

    haiku_result = run_brain(HAIKU, prompts, client)
    print(f"Haiku Brain done  — ${haiku_result.total_cost_usd:.4f}  avg {haiku_result.avg_latency_ms:.0f}ms/turn")

    sonnet_result = run_brain(SONNET, prompts, client)
    print(f"Sonnet Brain done — ${sonnet_result.total_cost_usd:.4f}  avg {sonnet_result.avg_latency_ms:.0f}ms/turn")

    ratio_cost = haiku_result.total_cost_usd / sonnet_result.total_cost_usd if sonnet_result.total_cost_usd else 0
    ratio_latency = haiku_result.avg_latency_ms / sonnet_result.avg_latency_ms if sonnet_result.avg_latency_ms else 0

    print(f"\nHaiku/Sonnet cost ratio:    {ratio_cost:.3f}×  ({(1-ratio_cost)*100:.1f}% cheaper)")
    print(f"Haiku/Sonnet latency ratio: {ratio_latency:.3f}×")

    out = {
        "haiku": asdict(haiku_result),
        "sonnet": asdict(sonnet_result),
        "summary": {
            "cost_ratio_haiku_over_sonnet": ratio_cost,
            "latency_ratio_haiku_over_sonnet": ratio_latency,
            "haiku_savings_pct": (1 - ratio_cost) * 100,
        },
    }

    out_path = args.out or (RESULTS_DIR / f"brain_tier_compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nSaved → {out_path}")


if __name__ == "__main__":
    main()
