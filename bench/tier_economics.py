#!/usr/bin/env python3
"""Tier economics — the original burnless thesis, measured against the real API.

The spine bench asks "does rolling memory make one conversation cheaper?".
This asks the older, bigger question: is it cheaper to have an expensive model
DO all the work, or to have it PLAN and hand execution to cheap workers that
carry no history and report back one line?

Four arms over an identical scripted task (the same N subtasks, the same
payloads, the same outputs):

  A  solo-sonnet   one conversation on Sonnet; every subtask done inline, so
                   every bulky tool result stays in context for every later
                   turn. Cache breakpoint at the tail, like a good client.
  B  solo-opus     same shape on Opus — the "just use the best model" baseline.
  C  burnless      Opus maestro that never sees a tool result: each subtask
                   goes to a FRESH Haiku worker with no history, and only a
                   one-line capsule comes back into the maestro's context.
  D  burnless+local  the configuration actually in daily use — same as C with
                   the worker run by a local model. Worker API cost is zero by
                   construction, so this is C's maestro spend alone. Derived
                   arithmetic, not a separate run: it measures cost, and says
                   nothing about local-model quality.

What makes the comparison honest: all four arms run the same subtasks with the
same payload bytes, every arm gets prompt caching placed the way a competent
client would place it, and every number reported is the provider's own usage
block priced at the published rate — never an estimate.

Costs money. Requires ANTHROPIC_API_KEY. Run --dry-run first: it prints the
projected spend without making a single call, and --max-usd aborts the run if
the measured spend passes the ceiling.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

API = "https://api.anthropic.com/v1/messages"

# per MTok, published rates. cache write (5m) is 1.25x input, cache read 0.1x.
MODELS = {
    "opus": {"id": "claude-opus-5", "in": 5.00, "out": 25.00, "effort": True, "thinking": True},
    "sonnet": {"id": "claude-sonnet-5", "in": 3.00, "out": 15.00, "effort": True, "thinking": True},
    "haiku": {"id": "claude-haiku-4-5", "in": 1.00, "out": 5.00, "effort": False, "thinking": False},
}
CACHE_WRITE_MULT, CACHE_READ_MULT = 1.25, 0.10

MAESTRO_SYSTEM = """You are the burnless Maestro. You never execute work yourself.

You hold the plan and a list of one-line capsules describing what workers have
already done. Workers are ephemeral: each receives one task with no history,
writes its full output to disk, and returns a single line. You never see a
worker's raw output — only its capsule.

Given the capsules so far and the next subtask, state in one short sentence what
the worker should do. Do not restate the capsules."""

SOLO_SYSTEM = """You are a senior engineer working through a task list in one
session. You read files, run tests, and edit code yourself, reporting briefly
after each step."""


def payload(kind: str, target: str, scale: int) -> str:
    if kind == "test":
        head = "\n".join(f"tests/test_mod_{i}.py ...............  [{i * 3:>3}%]" for i in range(40 * scale))
        return f"{head}\n\n=== 214 passed, 3 skipped in 18.44s ===\n"
    body = "\n".join(
        f"{i:>4}  def handler_{i}(payload, *, retries=3):  # {target}\n"
        f"          if not payload: return None\n"
        f"          return dispatch(payload, retries=retries)"
        for i in range(70 * scale)
    )
    return f"     1\t# {target}\n{body}\n"


SUBTASKS = [
    ("audit the tier resolver for the routing migration", "src/burnless/routing.py", "read"),
    ("fix the off-by-one in the capsule budget", "src/burnless/compression.py", "read"),
    ("run the suite and report regressions", "tests/", "test"),
    ("check why the warm pool drops its prefix", "src/burnless/warm_session.py", "read"),
    ("map the audit-graph refactor surface", "src/burnless/audit_graph.py", "read"),
    ("verify the capsule spine invariants hold", "tests/", "test"),
]


def capsule(i: int, target: str) -> str:
    return f"gold audit {target} :: OK verified 4/4 [ref:d{i:03d}]"


def worker_report(target: str) -> str:
    return (
        f"Done on {target}: the dispatch path assumed a non-empty payload, so the retry "
        f"counter never reset. Fixed at the call site, covered by a regression test."
    )


def call(client: httpx.Client, key: str, model_key: str, system: str, messages: list, max_tokens: int = 8) -> dict:
    """One request. Thinking is disabled and effort pinned low wherever the
    model allows it: this bench measures how much CONTEXT each architecture
    carries, and letting thinking spend vary would drown that signal."""
    m = MODELS[model_key]
    body = {
        "model": m["id"],
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
    }
    if m["thinking"]:
        body["thinking"] = {"type": "disabled"}
    if m["effort"]:
        body["output_config"] = {"effort": "low"}
    r = client.post(
        API,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json=body,
        timeout=180.0,
    )
    r.raise_for_status()
    return r.json().get("usage") or {}


def cost(model_key: str, u: dict) -> float:
    m = MODELS[model_key]
    return (
        u.get("input_tokens", 0) * m["in"]
        + u.get("cache_creation_input_tokens", 0) * m["in"] * CACHE_WRITE_MULT
        + u.get("cache_read_input_tokens", 0) * m["in"] * CACHE_READ_MULT
        + u.get("output_tokens", 0) * m["out"]
    ) / 1_000_000


def mark_tail(messages: list, prev: int | None = None) -> list:
    """Roll TWO breakpoints: the new tail and the previous turn's tail.

    Marking only the tail is not what a real client does, and it is not
    harmless: measured against the API, Opus 5 then re-writes the whole prefix
    every turn (cache_read 0) while Sonnet 5 still reads. Keeping the prior
    breakpoint alive is what lets the next turn find the cached prefix."""
    # Normalize EVERY message to block form first. Converting only the marked
    # ones changes `content` from a string to a list on some turns and not
    # others, which changes the serialized prefix bytes and silently costs the
    # cache — the exact class of bug this bench exists to catch.
    out = []
    for m in messages:
        c = m["content"]
        c = [{"type": "text", "text": c}] if isinstance(c, str) else [dict(b) for b in c]
        out.append({**m, "content": c})

    def stamp(idx):
        c = out[idx]["content"]
        c[-1] = dict(c[-1], cache_control={"type": "ephemeral"})

    stamp(len(out) - 1)
    if prev is not None and 0 <= prev < len(out) - 1:
        stamp(prev)
    return out


def run_solo(client, key, model_key, subtasks, scale, run, log) -> list[dict]:
    """Everything in one conversation: bulky tool results accumulate forever."""
    system = SOLO_SYSTEM + f"\n\nsession-nonce: solo-{model_key}-{run}\n"
    history: list = []
    rows = []
    prev_tail = None
    for i, (ask, target, kind) in enumerate(subtasks):
        history.append({"role": "user", "content": f"Step {i}: {ask} ({target})."})
        u = call(client, key, model_key, system, mark_tail(history, prev_tail))
        prev_tail = len(history) - 1
        rows.append({"step": i, "model": model_key, "usd": cost(model_key, u), **u})
        log(model_key.upper(), i, model_key, u)
        history += [
            {"role": "assistant", "content": [
                {"type": "text", "text": f"Looking at {target}."},
                {"type": "tool_use", "id": f"t{i}", "name": "Read" if kind == "read" else "Bash",
                 "input": {"path": target}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": payload(kind, target, scale)}]},
            {"role": "assistant", "content": worker_report(target)},
        ]
    return rows


def run_burnless(client, key, subtasks, scale, run, log) -> tuple[list[dict], list[dict]]:
    """Maestro holds capsules only; each subtask is a fresh worker with no history."""
    system = MAESTRO_SYSTEM + f"\n\nsession-nonce: maestro-{run}\n"
    maestro: list = []
    m_rows, w_rows = [], []
    prev_tail = None
    for i, (ask, target, kind) in enumerate(subtasks):
        maestro.append({"role": "user", "content": f"Next subtask: {ask} ({target})."})
        u = call(client, key, "opus", system, mark_tail(maestro, prev_tail))
        prev_tail = len(maestro) - 1
        m_rows.append({"step": i, "model": "opus", "usd": cost("opus", u), **u})
        log("MAESTRO", i, "opus", u)

        # the worker: one task, no history, full payload in, one line out
        wu = call(client, key, "haiku", "You are a burnless worker. One task, no history. "
                  "Do the work and return a single-line capsule.",
                  [{"role": "user", "content": f"Task: {ask} ({target}).\n\n"
                    f"Input:\n{payload(kind, target, scale)}"}])
        w_rows.append({"step": i, "model": "haiku", "usd": cost("haiku", wu), **wu})
        log("worker", i, "haiku", wu)

        maestro.append({"role": "assistant", "content": capsule(i, target)})
    return m_rows, w_rows


def total(rows: list[dict]) -> dict:
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    t = {k: sum(r.get(k, 0) for r in rows) for k in keys}
    t["usd"] = sum(r["usd"] for r in rows)
    t["prompt"] = t["input_tokens"] + t["cache_read_input_tokens"] + t["cache_creation_input_tokens"]
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=6)
    ap.add_argument("--scale", type=int, default=1, help="payload size multiplier per subtask")
    ap.add_argument("--max-usd", type=float, default=1.00, help="abort mid-run if spend passes this")
    ap.add_argument("--dry-run", action="store_true", help="project the spend, make no calls")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    subtasks = [SUBTASKS[i % len(SUBTASKS)] for i in range(args.steps)]
    approx = lambda s: max(1, len(s) // 4)  # noqa: E731

    if args.dry_run:
        # rough projection from payload sizes, before spending anything
        pay = sum(approx(payload(k, t, args.scale)) for _, t, k in subtasks)
        solo_prompt = sum((pay * (i + 1)) // len(subtasks) for i in range(len(subtasks)))
        print(f"projection for {args.steps} subtasks, scale {args.scale} (no calls made):")
        print(f"  payload tokens total          ~{pay:,}")
        print(f"  solo-arm prompt tokens        ~{solo_prompt:,} each (context accumulates)")
        print(f"  A solo-sonnet worst case      ~${solo_prompt * MODELS['sonnet']['in'] / 1e6:.4f}")
        print(f"  B solo-opus   worst case      ~${solo_prompt * MODELS['opus']['in'] / 1e6:.4f}")
        print(f"  C burnless maestro (capsules) ~${len(subtasks) * 2000 * MODELS['opus']['in'] / 1e6:.4f}")
        print(f"  C burnless workers (haiku)    ~${pay * MODELS['haiku']['in'] / 1e6:.4f}")
        print("  (worst case = no cache hits; real runs come in well under this)")
        return 0

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 2
    run = args.run_id or str(int(time.time()))  # fresh nonce: never inherit a prior run's cache
    spent = [0.0]

    def log(label, i, model_key, u):
        c = cost(model_key, u)
        spent[0] += c
        billed = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                  + u.get("cache_creation_input_tokens", 0))
        print(f"  {label:<8} {i:>2}  prompt {billed:>7,}  read {u.get('cache_read_input_tokens', 0):>7,}"
              f"  write {u.get('cache_creation_input_tokens', 0):>6,}  ${c:.5f}   [run ${spent[0]:.4f}]")
        if spent[0] > args.max_usd:
            raise SystemExit(f"aborting: spend ${spent[0]:.4f} passed --max-usd {args.max_usd}")

    client = httpx.Client()
    print(f"tier economics — {args.steps} subtasks, scale {args.scale}, ceiling ${args.max_usd}\n")
    print("arm A: solo sonnet (all work inline, context accumulates)")
    a = run_solo(client, key, "sonnet", subtasks, args.scale, run, log)
    print("\narm B: solo opus (same shape, best model)")
    b = run_solo(client, key, "opus", subtasks, args.scale, run, log)
    print("\narm C: burnless — opus maestro on capsules + fresh haiku workers")
    m, w = run_burnless(client, key, subtasks, args.scale, run, log)
    client.close()

    ta, tb = total(a), total(b)
    tm, tw = total(m), total(w)
    tc = {"usd": tm["usd"] + tw["usd"], "prompt": tm["prompt"] + tw["prompt"]}
    td = {"usd": tm["usd"], "prompt": tm["prompt"]}  # local workers: no API cost

    print("\n" + "=" * 76)
    print(f"{'arm':<34}{'prompt tok':>14}{'USD':>12}{'vs solo-opus':>16}")
    for label, t in (
        ("A  solo sonnet", ta),
        ("B  solo opus", tb),
        ("C  burnless opus + haiku", tc),
        ("D  burnless opus + local worker", td),
    ):
        delta = f"{(tb['usd'] - t['usd']) / tb['usd'] * 100:+.1f}%" if tb["usd"] else "—"
        print(f"{label:<34}{t['prompt']:>14,}{t['usd']:>12.5f}{delta:>16}")
    print(f"\n   (C splits as maestro ${tm['usd']:.5f} + workers ${tw['usd']:.5f})")
    print("   D is C's maestro alone — local workers cost no API tokens. Cost only; says")
    print("   nothing about local-model quality, which needs a separate eval.")
    print(f"\ntotal spend this run: ${spent[0]:.4f}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"steps": args.steps, "scale": args.scale, "run": run,
             "solo_sonnet": a, "solo_opus": b, "maestro": m, "workers": w}, indent=2))
        print(f"raw rows → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
