"""Replay vs Capsule — the core curve test (O(N²) vs O(N)).

Mode A (replay): each turn sends prior turns concatenated as transcript + new prompt.
Mode B (capsule): each turn sends 80-char summaries of prior turns + new prompt.

Same N-turn task in both modes. Measures cumulative cost across turns.
The curve shape is the proof: A grows superlinearly, B grows ~linearly.

Run order: after `cache_warm_check.py` and `cache_invalidation.py` confirm mechanics.
Estimated runtime: ~3 min per mode at N=10 (Haiku).
Output: ~/.burnless/test_data/{ts}/replay_vs_capsule.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def call_claude(prompt: str, model: str) -> dict:
    proc = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--model", model, prompt],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed: {proc.stderr[:500]}")
    return json.loads(proc.stdout.strip().splitlines()[0])


def extract(r: dict) -> dict:
    u = r.get("usage", {}) or {}
    return {
        "input_tokens": u.get("input_tokens", 0),
        "output_tokens": u.get("output_tokens", 0),
        "cache_read": u.get("cache_read_input_tokens", 0),
        "cache_create": u.get("cache_creation_input_tokens", 0),
        "cost_usd": r.get("total_cost_usd", 0.0),
        "result_text": (r.get("result") or "")[:500],
    }


TURN_TASKS = {
    "migration": (
        "Write a detailed paragraph (around 300 tokens, roughly 200 words) explaining "
        "step {k} of an imaginary technical migration plan. Be specific: invent realistic "
        "components, dependencies, risks, and verification commands. Do NOT use placeholders "
        "like 'XYZ' — write concrete prose. End with a one-line summary."
    ),
    "code": (
        "Implement step {k} of a Python CLI tool, building on the prior steps. Add ONE "
        "well-formed function (~30-50 lines): realistic logic, type hints, a docstring, and "
        "error handling. Output ONLY the new function's code (no prose). End with a one-line "
        "'# summary: ...' comment."
    ),
}
TURN_TASK = TURN_TASKS["migration"]


def run_replay(turns: int, model: str) -> list[dict]:
    history: list[tuple[str, str]] = []
    per_turn: list[dict] = []
    cumulative = 0.0
    for k in range(1, turns + 1):
        parts: list[str] = []
        for u_msg, r_msg in history:
            parts.append(f"User: {u_msg}\nAssistant: {r_msg}")
        new_user = TURN_TASK.format(k=k)
        parts.append(f"User: {new_user}\nAssistant: ")
        prompt = "\n\n".join(parts)

        s = extract(call_claude(prompt, model))
        cumulative += s["cost_usd"]
        per_turn.append({"turn": k, "cumulative_cost": round(cumulative, 6), **s})
        history.append((new_user, s["result_text"]))
    return per_turn


def run_capsule(turns: int, model: str) -> list[dict]:
    capsules: list[str] = []
    per_turn: list[dict] = []
    cumulative = 0.0
    for k in range(1, turns + 1):
        parts = ["Prior steps (compressed):"] if capsules else ["No prior steps yet."]
        for i, c in enumerate(capsules, start=1):
            parts.append(f"  step {i}: {c}")
        new_user = TURN_TASK.format(k=k)
        parts.append(f"\nNow: {new_user}")
        prompt = "\n".join(parts)

        s = extract(call_claude(prompt, model))
        cumulative += s["cost_usd"]
        per_turn.append({"turn": k, "cumulative_cost": round(cumulative, 6), **s})
        capsules.append(s["result_text"][:80].replace("\n", " ").strip())
    return per_turn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--task", choices=["migration", "code"], default="migration")
    args = ap.parse_args()
    global TURN_TASK
    TURN_TASK = TURN_TASKS[args.task]

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path.home() / ".burnless" / "test_data" / ts
    out.mkdir(parents=True, exist_ok=True)

    print(f"=== Mode A: Replay (transcript grows every turn) ===")
    a = run_replay(args.turns, args.model)
    for t in a:
        print(f"  turn {t['turn']:>2}  in={t['input_tokens']:>5}  out={t['output_tokens']:>4}  cum=${t['cumulative_cost']:.4f}")

    print(f"\n=== Mode B: Capsule (history compressed every turn) ===")
    b = run_capsule(args.turns, args.model)
    for t in b:
        print(f"  turn {t['turn']:>2}  in={t['input_tokens']:>5}  out={t['output_tokens']:>4}  cum=${t['cumulative_cost']:.4f}")

    final_a = a[-1]["cumulative_cost"]
    final_b = b[-1]["cumulative_cost"]
    ratio = (final_a / final_b) if final_b > 0 else float("inf")
    print(f"\nfinal: replay=${final_a:.4f}  capsule=${final_b:.4f}  ratio={ratio:.2f}x")

    (out / "replay_vs_capsule.json").write_text(json.dumps({
        "model": args.model, "turns": args.turns,
        "mode_a_replay": a, "mode_b_capsule": b,
        "final_replay_cost": final_a, "final_capsule_cost": final_b, "ratio": ratio,
    }, indent=2))
    print(f"saved: {out}/replay_vs_capsule.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
