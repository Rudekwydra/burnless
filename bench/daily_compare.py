"""
daily_compare.py — paired-measurement bench: compressed (Burnless) vs raw (no Burnless).

Run a fixed batch of test prompts in two configurations:
  1. Compressed: prompt -> Haiku encoder -> Sonnet brain (capsule input) -> Haiku decoder
  2. Raw:        prompt -> Sonnet brain (full input as-is, full output)

Same prompts each run = control. Variation in output across days = signal.
Save to comparison_data/<date>/<batch_id>.json with full usage breakdown.

Cost cap: configurable max-USD per batch (default $0.50). Aborts mid-batch if exceeded.

Designed to run via cron / launchd at low-traffic hour:
    0 4 * * *  ANTHROPIC_API_KEY=... python /path/to/daily_compare.py --batch-id daily

Usage:
    python bench/daily_compare.py --help
    python bench/daily_compare.py --batch-id manual1
    python bench/daily_compare.py --batch-id daily --max-usd 0.30
    python bench/daily_compare.py --prompts custom_prompts.json

Output:
    bench/comparison_data/<YYYY-MM-DD>/<batch_id>.json
    Contains per-prompt usage for both configurations + summary deltas.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Anthropic pricing as of 2026-01 (USD per million tokens).
# Update if pricing changes — these drive the cost cap.
PRICING = {
    "claude-opus-4-7":     {"input": 15.0, "output": 75.0,  "cache_write_1h": 30.0,  "cache_read": 1.50},
    "claude-sonnet-4-6":   {"input": 3.0,  "output": 15.0,  "cache_write_1h": 6.0,   "cache_read": 0.30},
    "claude-haiku-4-5":    {"input": 1.0,  "output": 5.0,   "cache_write_1h": 2.0,   "cache_read": 0.10},
}


# Default fixed prompt set — varied tones and lengths for representative coverage.
# Small enough to keep costs low, varied enough to stress-test response patterns.
DEFAULT_PROMPTS = [
    # Imperative, technical, short
    "List the top 3 causes of memory leaks in long-running Python services.",
    # Casual, ambiguous
    "what's the deal with rust borrow checker? seems annoying",
    # Formal, structured request
    "Please provide a concise overview of the BM25 ranking function and its three core parameters.",
    # Bug-report style
    "TypeError: 'NoneType' object is not subscriptable at line 42 of api.py — common cause?",
    # Tentative, ideation
    "and if we used a graph database for session memory instead of vector embeddings... thoughts?",
    # PT-BR formal
    "Explique de forma sucinta o que é prompt caching e em quais cenários ele se paga.",
    # PT-BR casual
    "véi, qual a melhor forma de fazer rate limiting num express server?",
    # Diminutivo PT-BR
    "tipo, vê um cadinho a parada do redis pubsub vs streams pra mim?",
    # Code review request
    "Review this approach: storing JWT in localStorage with refresh-on-401. Concerns?",
    # Telegraphic
    "haiku 4.5 vs sonnet 4.6 latency p50 typical workload",
]


@dataclass
class CallResult:
    role: str  # "encoder" | "brain" | "decoder" | "raw_brain"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    output_text: str = ""
    error: str | None = None


@dataclass
class PromptResult:
    prompt: str
    prompt_idx: int
    compressed: list[CallResult] = field(default_factory=list)
    raw: list[CallResult] = field(default_factory=list)

    def total_cost_compressed(self) -> float:
        return sum(c.cost_usd for c in self.compressed)

    def total_cost_raw(self) -> float:
        return sum(c.cost_usd for c in self.raw)

    def total_output_tokens_compressed(self) -> int:
        # Decoder output is what user sees in compressed mode.
        decoder_calls = [c for c in self.compressed if c.role == "decoder"]
        return decoder_calls[-1].output_tokens if decoder_calls else 0

    def total_output_tokens_raw(self) -> int:
        return self.raw[-1].output_tokens if self.raw else 0


def call_cost(usage: dict[str, int], model: str) -> float:
    """Compute USD cost from a usage dict and model id."""
    if model not in PRICING:
        return 0.0
    p = PRICING[model]
    return (
        usage.get("input_tokens", 0) * p["input"] / 1_000_000
        + usage.get("output_tokens", 0) * p["output"] / 1_000_000
        + usage.get("cache_creation_input_tokens", 0) * p["cache_write_1h"] / 1_000_000
        + usage.get("cache_read_input_tokens", 0) * p["cache_read"] / 1_000_000
    )


def usage_dict(response: Any) -> dict[str, int]:
    u = getattr(response, "usage", None)
    if not u:
        return {}
    return {
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
    }


def response_text(response: Any) -> str:
    parts = []
    for block in getattr(response, "content", []):
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
    return "\n".join(parts).strip()


def run_compressed(
    client: Any,
    prompt: str,
    *,
    encoder_model: str = "claude-haiku-4-5",
    brain_model: str = "claude-sonnet-4-6",
    decoder_model: str = "claude-haiku-4-5",
) -> list[CallResult]:
    """Compressed pipeline: encoder -> brain (capsule input) -> decoder.

    Uses Burnless codec modules for actual encoder/decoder logic so the
    measurement reflects production behavior.
    """
    from burnless.codec import encoder as enc_mod
    from burnless.codec import decoder as dec_mod

    results: list[CallResult] = []

    # Stage 1: encoder
    t0 = time.monotonic()
    try:
        capsule, _score = enc_mod.encode(prompt, model=encoder_model, client=client)
    except Exception as e:
        results.append(CallResult(role="encoder", model=encoder_model, error=str(e)))
        return results
    enc_duration = time.monotonic() - t0
    # We don't have direct access to encoder's response.usage from inside
    # encode(); estimate via char counts as a fallback. Production
    # instrumentation in metrics.py captures the real usage when called
    # via the live system.
    encoder_input_estimate = max(int(len(prompt) / 3.5), 1)
    encoder_output_estimate = max(int(len(capsule) / 3.5), 1)
    enc_usage = {
        "input_tokens": encoder_input_estimate,
        "output_tokens": encoder_output_estimate,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    results.append(
        CallResult(
            role="encoder",
            model=encoder_model,
            input_tokens=enc_usage["input_tokens"],
            output_tokens=enc_usage["output_tokens"],
            cost_usd=call_cost(enc_usage, encoder_model),
            duration_s=enc_duration,
            output_text=capsule,
        )
    )

    # Stage 2: brain — receives capsule as user message, no transcript history.
    t0 = time.monotonic()
    try:
        brain_resp = client.messages.create(
            model=brain_model,
            max_tokens=500,
            system=[
                {
                    "type": "text",
                    "text": (
                        "You are a brain in a Burnless session. You receive a capsule "
                        "(compressed user intent) and must respond with a similarly "
                        "compact capsule. No prose. No preamble. Be concise."
                    ),
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            messages=[{"role": "user", "content": capsule}],
        )
    except Exception as e:
        results.append(CallResult(role="brain", model=brain_model, error=str(e)))
        return results
    brain_duration = time.monotonic() - t0
    brain_usage = usage_dict(brain_resp)
    brain_text = response_text(brain_resp)
    results.append(
        CallResult(
            role="brain",
            model=brain_model,
            input_tokens=brain_usage["input_tokens"],
            output_tokens=brain_usage["output_tokens"],
            cache_creation_input_tokens=brain_usage["cache_creation_input_tokens"],
            cache_read_input_tokens=brain_usage["cache_read_input_tokens"],
            cost_usd=call_cost(brain_usage, brain_model),
            duration_s=brain_duration,
            output_text=brain_text,
        )
    )

    # Stage 3: decoder — expand brain capsule into prose for human.
    t0 = time.monotonic()
    try:
        expanded = dec_mod.decode(
            brain_text, model=decoder_model, client=client, voice_sample=prompt
        )
    except Exception as e:
        results.append(CallResult(role="decoder", model=decoder_model, error=str(e)))
        return results
    dec_duration = time.monotonic() - t0
    # Same fallback estimate for decoder.
    decoder_input_estimate = max(int(len(brain_text) / 3.5), 1)
    decoder_output_estimate = max(int(len(expanded) / 3.5), 1)
    dec_usage = {
        "input_tokens": decoder_input_estimate,
        "output_tokens": decoder_output_estimate,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    results.append(
        CallResult(
            role="decoder",
            model=decoder_model,
            input_tokens=dec_usage["input_tokens"],
            output_tokens=dec_usage["output_tokens"],
            cost_usd=call_cost(dec_usage, decoder_model),
            duration_s=dec_duration,
            output_text=expanded,
        )
    )

    return results


def run_raw(
    client: Any,
    prompt: str,
    *,
    brain_model: str = "claude-sonnet-4-6",
) -> list[CallResult]:
    """Raw pipeline: prompt sent directly to Sonnet, no compression, no Burnless.

    This is the control: what a default Claude session would do with the same
    input. The output is what would be billed at full output rate.
    """
    t0 = time.monotonic()
    try:
        resp = client.messages.create(
            model=brain_model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return [CallResult(role="raw_brain", model=brain_model, error=str(e))]
    duration = time.monotonic() - t0
    u = usage_dict(resp)
    text = response_text(resp)
    return [
        CallResult(
            role="raw_brain",
            model=brain_model,
            input_tokens=u["input_tokens"],
            output_tokens=u["output_tokens"],
            cache_creation_input_tokens=u["cache_creation_input_tokens"],
            cache_read_input_tokens=u["cache_read_input_tokens"],
            cost_usd=call_cost(u, brain_model),
            duration_s=duration,
            output_text=text,
        )
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily compressed-vs-raw comparison batch.")
    ap.add_argument(
        "--batch-id",
        default=f"batch-{uuid.uuid4().hex[:8]}",
        help="Identifier for this batch run (default: random)",
    )
    ap.add_argument(
        "--prompts",
        type=Path,
        default=None,
        help="JSON file with custom prompts list (default: built-in DEFAULT_PROMPTS)",
    )
    ap.add_argument(
        "--max-usd",
        type=float,
        default=0.50,
        help="Hard cost cap per batch in USD (default: $0.50). Batch aborts mid-run if exceeded.",
    )
    ap.add_argument(
        "--encoder-model",
        default="claude-haiku-4-5",
    )
    ap.add_argument(
        "--brain-model",
        default="claude-sonnet-4-6",
    )
    ap.add_argument(
        "--decoder-model",
        default="claude-haiku-4-5",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "comparison_data",
        help="Where to write batch results (default: bench/comparison_data/)",
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set in environment", file=sys.stderr)
        return 2

    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic SDK not installed (pip install anthropic)", file=sys.stderr)
        return 2

    client = anthropic.Anthropic()

    if args.prompts:
        prompts = json.loads(args.prompts.read_text())
    else:
        prompts = list(DEFAULT_PROMPTS)

    print(f"daily_compare batch={args.batch_id}")
    print(f"  prompts: {len(prompts)}  cost_cap: ${args.max_usd:.2f}")
    print(f"  encoder: {args.encoder_model}  brain: {args.brain_model}  decoder: {args.decoder_model}")
    print()

    results: list[PromptResult] = []
    cumulative_cost = 0.0
    aborted = False

    for i, prompt in enumerate(prompts):
        if cumulative_cost >= args.max_usd:
            print(f"  [abort] cost cap ${args.max_usd:.2f} reached at prompt {i}")
            aborted = True
            break

        print(f"  [{i+1}/{len(prompts)}] {prompt[:60]}{'...' if len(prompt) > 60 else ''}")
        pr = PromptResult(prompt=prompt, prompt_idx=i)

        # Compressed pipeline
        pr.compressed = run_compressed(
            client, prompt,
            encoder_model=args.encoder_model,
            brain_model=args.brain_model,
            decoder_model=args.decoder_model,
        )
        c_cost = pr.total_cost_compressed()
        cumulative_cost += c_cost

        # Raw pipeline (control)
        pr.raw = run_raw(client, prompt, brain_model=args.brain_model)
        r_cost = pr.total_cost_raw()
        cumulative_cost += r_cost

        c_out = pr.total_output_tokens_compressed()
        r_out = pr.total_output_tokens_raw()
        ratio = (r_out / c_out) if c_out > 0 else float('nan')
        print(f"      compressed=${c_cost:.5f} ({c_out}tok)  raw=${r_cost:.5f} ({r_out}tok)  raw/compressed={ratio:.2f}x")

        results.append(pr)

    # Aggregate summary
    total_compressed_cost = sum(r.total_cost_compressed() for r in results)
    total_raw_cost = sum(r.total_cost_raw() for r in results)
    total_compressed_out = sum(r.total_output_tokens_compressed() for r in results)
    total_raw_out = sum(r.total_output_tokens_raw() for r in results)
    avg_ratio = (
        sum((r.total_output_tokens_raw() / r.total_output_tokens_compressed())
            for r in results
            if r.total_output_tokens_compressed() > 0)
        / max(len([r for r in results if r.total_output_tokens_compressed() > 0]), 1)
    )

    summary = {
        "batch_id": args.batch_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "models": {
            "encoder": args.encoder_model,
            "brain": args.brain_model,
            "decoder": args.decoder_model,
        },
        "prompts_run": len(results),
        "prompts_total": len(prompts),
        "aborted_by_cost_cap": aborted,
        "cost_cap_usd": args.max_usd,
        "totals": {
            "compressed_cost_usd": round(total_compressed_cost, 6),
            "raw_cost_usd": round(total_raw_cost, 6),
            "cost_savings_usd": round(total_raw_cost - total_compressed_cost, 6),
            "cost_savings_pct": round(
                (total_raw_cost - total_compressed_cost) / total_raw_cost * 100, 2
            ) if total_raw_cost > 0 else 0,
            "compressed_output_tokens": total_compressed_out,
            "raw_output_tokens": total_raw_out,
            "avg_raw_to_compressed_output_ratio": round(avg_ratio, 3),
        },
        "per_prompt": [
            {
                "prompt": r.prompt,
                "prompt_idx": r.prompt_idx,
                "compressed": [asdict(c) for c in r.compressed],
                "raw": [asdict(c) for c in r.raw],
                "summary": {
                    "compressed_cost_usd": round(r.total_cost_compressed(), 6),
                    "raw_cost_usd": round(r.total_cost_raw(), 6),
                    "compressed_output_tokens": r.total_output_tokens_compressed(),
                    "raw_output_tokens": r.total_output_tokens_raw(),
                },
            }
            for r in results
        ],
    }

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = args.output_dir / today
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.batch_id}.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    print()
    print(f"=== Summary ===")
    print(f"  prompts run: {len(results)} / {len(prompts)}{'  [aborted]' if aborted else ''}")
    print(f"  compressed total cost: ${total_compressed_cost:.5f}")
    print(f"  raw total cost:        ${total_raw_cost:.5f}")
    print(f"  savings:               ${total_raw_cost - total_compressed_cost:.5f}  ({summary['totals']['cost_savings_pct']:.1f}%)")
    print(f"  output ratio (raw/compressed): {avg_ratio:.2f}x average")
    print(f"  saved to: {out_path}")
    return 0 if not aborted else 1


if __name__ == "__main__":
    sys.exit(main())
