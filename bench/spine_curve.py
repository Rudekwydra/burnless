#!/usr/bin/env python3
"""Spine curve — per-turn input growth with the rolling spine engaged.

Measures the honest v1 success criterion from _design/SPINE_PROXY_2026-07-31.md:
in a long session the per-request input should grow O(sum of capsules) instead
of O(sum of verbatim). No API key, no network, no mocks of the transform: every
turn goes through the real SpineProxy.rewrite seam exactly as the HTTP server
applies it, with the real background compressor freezing capsules between
turns (the "user think time" the design relies on).

The simulated conversation is deterministic (seeded) and deliberately mixed:
message sizes vary widely and a couple of turns run tool_use/tool_result
loops, like a real Claude Code session. The client resends the full verbatim
history every turn.

Token counts are an approximation via tiktoken cl100k_base over the exact
serialized request body; if tiktoken (or its vocab download) is unavailable
the script fails open to chars/4 and says so.

Usage:
    python bench/spine_curve.py             # 20 turns
    python bench/spine_curve.py --turns 40 --seed 7
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path

try:
    from burnless.proxy.server import SpineProxy
except ImportError:  # running from a checkout without the package installed
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from burnless.proxy.server import SpineProxy

TOOL_TURNS = {5, 12}  # deterministic agentic loops in the script

WORDS = (
    "refactor the spine capsule ledger cache prefix token stream handler retry "
    "config parser deadline socket batch worker index shard replica quorum "
    "because instead must never always error failed fixed decided rollout "
    "src/burnless/proxy/spine.py tests/test_proxy_spine.py 502 4096 traceback "
    "append only frozen exchange verbatim history compressor absorb regime"
).split()


def _text(rng: random.Random, n_words: int, salt: str) -> str:
    """Deterministic filler with a unique salt so every exchange hashes fresh."""
    return f"[{salt}] " + " ".join(rng.choice(WORDS) for _ in range(n_words))


def _token_counter():
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return "tiktoken cl100k_base", lambda raw: len(
            enc.encode(raw.decode("utf-8"), disallowed_special=())
        )
    except Exception as exc:  # fail open: the curve still means something in chars/4
        print(f"note: tiktoken unavailable ({type(exc).__name__}); using chars/4", file=sys.stderr)
        return "chars/4", lambda raw: max(1, len(raw) // 4)


def _wait_compressor(proxy: SpineProxy, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while proxy.store.pending() and time.time() < deadline:
        time.sleep(0.01)


def _simulate_reply(history: list, rng: random.Random, turn: int) -> None:
    """Append this turn's assistant side to the verbatim history."""
    if turn in TOOL_TURNS:
        tid = f"toolu_bench_{turn}"
        history.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": _text(rng, 40, f"a{turn}")},
                    {"type": "tool_use", "id": tid, "name": "Bash",
                     "input": {"command": _text(rng, 10, f"c{turn}")}},
                ],
            }
        )
        history.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tid,
                     "content": _text(rng, 300, f"r{turn}")},
                ],
            }
        )
        history.append({"role": "assistant", "content": _text(rng, rng.randint(80, 200), f"f{turn}")})
    else:
        history.append({"role": "assistant", "content": _text(rng, rng.randint(80, 350), f"a{turn}")})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--turns", type=int, default=20, help="simulated turns (default 20)")
    ap.add_argument("--seed", type=int, default=7, help="rng seed (default 7)")
    args = ap.parse_args()

    counter_name, count = _token_counter()
    rng = random.Random(args.seed)

    with tempfile.TemporaryDirectory(prefix="burnless-spine-curve-") as tmp:
        proxy = SpineProxy(Path(tmp) / "proxy", upstream="http://127.0.0.1:1")
        history: list = []
        total_client = total_upstream = 0

        print(f"spine curve: {args.turns} turns, seed {args.seed}, tokens via {counter_name}")
        print(f"{'turn':>4}  {'client_tok':>10}  {'upstream_tok':>12}  {'saved%':>7}  {'cum_saved':>9}  note")
        for turn in range(1, args.turns + 1):
            history.append({"role": "user", "content": _text(rng, rng.randint(60, 300), f"q{turn}")})
            body = {"model": "claude-sonnet-4-5", "max_tokens": 1024, "messages": history}
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            out, stats = proxy.rewrite("/v1/messages", raw)

            client_tok, upstream_tok = count(raw), count(out)
            total_client += client_tok
            total_upstream += upstream_tok
            saved_pct = 100.0 * (client_tok - upstream_tok) / client_tok if client_tok else 0.0
            note = (
                f"spine absorbed={stats.get('absorbed')}"
                if stats.get("applied")
                else f"passthrough ({stats.get('reason')})"
            )
            print(
                f"{turn:>4}  {client_tok:>10}  {upstream_tok:>12}  {saved_pct:>6.1f}%  "
                f"{total_client - total_upstream:>9}  {note}"
            )

            _simulate_reply(history, rng, turn)
            _wait_compressor(proxy)  # user think time: capsules freeze off-path

        proxy.client.close()
        pct = 100.0 * (total_client - total_upstream) / total_client if total_client else 0.0
        print(
            f"summary: {args.turns} turns  client={total_client} tok  upstream={total_upstream} tok  "
            f"saved={total_client - total_upstream} tok ({pct:.1f}%)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
