#!/usr/bin/env python3
"""Spine proxy A/B against the real provider — the honest cost comparison.

Same scripted conversation, same model, same caching discipline; the only
difference between the arms is the proxy in the middle:

  A (baseline)  client → api.anthropic.com, full verbatim history,
                ephemeral breakpoint at the tail (what a normal client does)
  B (spine)     client → burnless proxy → api.anthropic.com, identical
                client behavior; the proxy substitutes older exchanges with
                frozen capsules and marks the spine's last block

Both arms are billed by the provider, and the provider's own usage block is
what we report — input, output, cache_read, cache_creation — plus the cost
those numbers imply. Cache-hit RATIO is not the interesting metric (a
well-behaved verbatim client also hits cache); the interesting metric is
absolute tokens and dollars, because the baseline's cached prefix grows with
verbatim history while the spine's grows with capsules.

The assistant turns are scripted, so both arms send byte-identical
conversations and the comparison is not polluted by sampling. Each call asks
for a token or two of real completion purely to obtain a usage block.

Costs money. Requires ANTHROPIC_API_KEY. Defaults are sized for cents.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from burnless.proxy.server import SpineProxy, _make_handler  # noqa: E402

API = "https://api.anthropic.com"
MODEL = "claude-haiku-4-5-20251001"

# per MTok, claude-haiku-4-5 (5-minute cache): write 1.25x input, read 0.1x
PRICE = {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10}

SYSTEM_CORE = """You are a senior engineer pairing on the burnless project.

Project doctrine (abridged):
- Tiers are roles, not models. gold/silver/bronze map to worker commands.
- Workers are ephemeral: one task, no history, full output to disk, one line back.
- Capsules are short on-disk records of a turn; the transcript is never replayed.
- The filesystem is the audit surface: execution claims are checked against
  real files and sizes before a delegation is marked OK.
- Compression separates three intents a single turn conflates: thinking is
  one-shot, execution returns a capsule, verbose goes to the human surface.
- Prompt caching is exact-prefix, not a volume discount. Any byte that moves
  in the cached region invalidates everything after it.
- Recovery is filesystem-first: pointers, not payloads; nothing is destroyed.
"""


def big_system(nonce: str) -> str:
    """A realistic system prompt, padded past the model's cache minimum.

    The nonce keeps the two arms on separate cache entries so arm B never
    inherits a prefix arm A warmed."""
    filler = "\n".join(
        f"- Rule {i}: prefer the smaller surface; state constraints the code cannot show; "
        f"fail open on any parse error; never mutate a cached byte; keep the ledger honest."
        for i in range(120)
    )
    return f"{SYSTEM_CORE}\n\nHouse rules:\n{filler}\n\nsession-nonce: {nonce}\n"


def _fake_file(path: str, lines: int) -> str:
    body = "\n".join(
        f"{i:>4}  def handler_{i}(payload, *, retries=3):  # {path}\n"
        f"          if not payload: return None\n"
        f"          return dispatch(payload, retries=retries)"
        for i in range(lines)
    )
    return f"     1\t# {path}\n{body}\n"


def _fake_test_output(n: int) -> str:
    head = "\n".join(f"tests/test_module_{i}.py ....................  [{i * 5:>3}%]" for i in range(n))
    return f"{head}\n\n=== 214 passed, 3 skipped in 18.44s ===\n"


def scripted_turns(n: int, scale: int = 1) -> list[list[dict]]:
    """Realistic agentic exchanges — the shape a human session actually has.

    A turn is: the human asks for work; the assistant reads/edits/runs
    something; bulky tool_result comes back; the assistant reports. The bulk
    lives in tool_result blocks, which is exactly what the spine replaces
    with one capsule once the exchange is behind us. Each returned item is a
    full exchange (list of messages), identical for both arms.
    """
    asks = [
        ("plan the migration of the routing layer to the new tier resolver",
         "src/burnless/routing.py", "read"),
        ("fix the off-by-one in the capsule budget and prove it with the suite",
         "src/burnless/compression.py", "test"),
        ("delegate the audit-graph refactor to a worker and report back",
         "src/burnless/audit_graph.py", "read"),
        ("check why the warm pool loses its prefix after an hour",
         "src/burnless/warm_session.py", "read"),
        ("run the full suite and tell me what regressed",
         "tests/", "test"),
        ("draft the plan for cross-machine capsule sync, no code yet",
         "src/burnless/exporting.py", "read"),
    ]
    out: list[list[dict]] = []
    for i in range(n):
        ask, target, kind = asks[i % len(asks)]
        payload = (
            _fake_test_output(30 * scale) if kind == "test" else _fake_file(target, 60 * scale)
        )
        out.append(
            [
                {
                    "role": "user",
                    "content": f"Turn {i}: {ask}. Be concrete about what you changed and why.",
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": f"Looking at {target} first."},
                        {
                            "type": "tool_use",
                            "id": f"tool_{i}",
                            "name": "Read" if kind == "read" else "Bash",
                            "input": {"file_path": target} if kind == "read" else {"command": "pytest -q"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": f"tool_{i}", "content": payload}],
                },
                {
                    "role": "assistant",
                    "content": (
                        f"Done on {target}: the dispatch path assumed a non-empty payload, so the "
                        f"retry counter never reset. Fixed at the call site and covered by a "
                        f"regression test. Nothing else in the tree depends on that branch."
                    ),
                },
            ]
        )
    return out


def mark_tail(messages: list) -> list:
    """What a well-behaved client does: cache everything up to the newest turn."""
    out = [dict(m) for m in messages]
    last = out[-1]
    content = last["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    else:
        content = [dict(b) for b in content]
    content[-1] = dict(content[-1], cache_control={"type": "ephemeral"})
    last["content"] = content
    return out


def call(client: httpx.Client, base: str, key: str, system: str, messages: list) -> dict:
    r = client.post(
        f"{base}/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={
            "model": MODEL,
            "max_tokens": 8,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": mark_tail(messages),
        },
        timeout=180.0,
    )
    r.raise_for_status()
    return r.json().get("usage") or {}


def cost(u: dict) -> float:
    return (
        u.get("input_tokens", 0) * PRICE["input"]
        + u.get("output_tokens", 0) * PRICE["output"]
        + u.get("cache_creation_input_tokens", 0) * PRICE["cache_write"]
        + u.get("cache_read_input_tokens", 0) * PRICE["cache_read"]
    ) / 1_000_000


def run_arm(label: str, base: str, key: str, turns: list, system: str, proxy: SpineProxy | None) -> list[dict]:
    client = httpx.Client()
    history: list = []
    rows = []
    for i, exchange in enumerate(turns):
        # the human's ask lands; the rest of the exchange is replayed as the
        # agentic work that answered it (tool_use → tool_result → report)
        history.append(exchange[0])
        usage = call(client, base, key, system, history)
        history.extend(exchange[1:])
        rows.append({"turn": i + 1, **usage, "usd": cost(usage)})
        billed = (
            usage.get("input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
        )
        print(
            f"  {label} turn {i + 1:>2}  prompt {billed:>7,}  "
            f"read {usage.get('cache_read_input_tokens', 0):>7,}  "
            f"write {usage.get('cache_creation_input_tokens', 0):>6,}  "
            f"fresh {usage.get('input_tokens', 0):>5,}  ${cost(usage):.5f}"
        )
        if proxy is not None:
            for _ in range(300):  # let the background compressor freeze new capsules
                if not proxy.store.pending():
                    break
                time.sleep(0.05)
    client.close()
    return rows


def totals(rows: list[dict]) -> dict:
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    t = {k: sum(r.get(k, 0) for r in rows) for k in keys}
    t["usd"] = sum(r["usd"] for r in rows)
    t["prompt"] = t["input_tokens"] + t["cache_read_input_tokens"] + t["cache_creation_input_tokens"]
    return t


def cold_resume(key: str, turns: list, history_len: int, proxy_base: str | None, run: str) -> dict:
    """The scenario a warm-loop benchmark never sees: you stepped away, the
    5-minute cache TTL lapsed, and the next turn pays full price for whatever
    the client still carries. Verbatim history is only cheap while it is
    cached; capsules are cheap always. One call per arm, fresh nonce."""
    client = httpx.Client()
    history: list = []
    for exchange in turns[:history_len]:
        history.extend(exchange)
    history.append({"role": "user", "content": "After the break: where did we land, and what is next?"})
    out = {}
    for label, base in (("A", API), ("B", proxy_base or API)):
        usage = call(client, base, key, big_system(f"cold-{label}-{run}"), history)
        out[label] = {**usage, "usd": cost(usage)}
    client.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=14)
    ap.add_argument("--scale", type=int, default=1,
                    help="exchange verbosity multiplier — real agent turns carry tool output, "
                         "not one-liners, and the spine's savings scale with what it replaces")
    ap.add_argument("--out", default=None, help="write the raw rows as JSON here")
    ap.add_argument("--run-id", default=None, help="cache-isolation nonce (default: time-based)")
    args = ap.parse_args()

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 2

    run = args.run_id or str(int(time.time()))  # fresh nonce: never inherit a prior run's cache
    turns = scripted_turns(args.turns, args.scale)
    print(f"spine A/B — {args.turns} turns, scale {args.scale}, {MODEL}, identical scripted conversation\n")

    print("arm A: verbatim history, direct to the provider")
    rows_a = run_arm("A", API, key, turns, big_system(f"arm-a-{run}"), None)

    with TemporaryDirectory() as td:
        proxy = SpineProxy(Path(td) / "proxy", upstream=API)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(proxy))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        print("\narm B: same conversation through the burnless spine proxy")
        rows_b = run_arm("B", base, key, turns, big_system(f"arm-b-{run}"), proxy)
        capsules = len(list((Path(td) / "proxy" / "capsules").glob("*.txt")))
        print("\ncold resume (cache TTL lapsed — the human stepped away):")
        cold = cold_resume(key, turns, args.turns, base, run)
        for lbl in ("A", "B"):
            c = cold[lbl]
            billed = c.get("input_tokens", 0) + c.get("cache_read_input_tokens", 0) + c.get("cache_creation_input_tokens", 0)
            print(f"  {lbl}  prompt {billed:>7,}  fresh {c.get('input_tokens', 0):>7,}  ${c['usd']:.5f}")
        gap = (cold["A"]["usd"] - cold["B"]["usd"]) / cold["A"]["usd"] * 100 if cold["A"]["usd"] else 0
        print(f"  → spine is {gap:.1f}% cheaper on the first turn back")
        srv.shutdown()

    ta, tb = totals(rows_a), totals(rows_b)
    print("\n" + "=" * 78)
    print(f"{'':<26}{'A verbatim':>16}{'B spine':>16}{'delta':>18}")
    for label, k in (
        ("prompt tokens billed", "prompt"),
        ("  cache read", "cache_read_input_tokens"),
        ("  cache written", "cache_creation_input_tokens"),
        ("  fresh input", "input_tokens"),
        ("output tokens", "output_tokens"),
    ):
        d = tb[k] - ta[k]
        pct = f"{(d / ta[k] * 100):+.1f}%" if ta[k] else "—"
        print(f"{label:<26}{ta[k]:>16,}{tb[k]:>16,}{pct:>18}")
    saved = (ta["usd"] - tb["usd"]) / ta["usd"] * 100 if ta["usd"] else 0
    verdict = f"{saved:.1f}% cheaper" if saved >= 0 else f"{-saved:.1f}% MORE"
    print(f"{'cost (USD)':<26}{ta['usd']:>16.5f}{tb['usd']:>16.5f}{verdict:>18}")
    print(f"\nfrozen capsules: {capsules}   total spend this run: ${ta['usd'] + tb['usd']:.4f}")

    if args.out:
        Path(args.out).write_text(
            json.dumps({"model": MODEL, "turns": args.turns, "arm_a": rows_a, "arm_b": rows_b}, indent=2)
        )
        print(f"raw rows → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
