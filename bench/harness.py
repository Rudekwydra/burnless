"""Shared bench harness — layer by layer, with the cache monitor built in.

Every paid bench imports this. The whole reason the earlier numbers were
unreliable is that prompt caching is emergent from byte-identical prefixes and
therefore invisible unless you inspect the provider's usage block on every
single turn. So the monitor is not optional decoration — it is the gate: a run
whose cache does not behave as declared FAILS loudly instead of printing a
confident-looking summary.

Layer discipline (what each layer must prove, bottom up):

  L0  provider cache primitive   does a byte-identical prefix, marked, read on
                                 the next request? per model, characterized in
                                 isolation — bench/layer0_cache.py
  L1  spine transform (offline)  pure dict surgery, append-only, fail-open —
                                 tests/test_proxy_spine.py (free, no API)
  L2  store + compressor (off)   content-addressed, first-write-wins, capsule
                                 shrinks — tests/test_proxy_*.py (free)
  L3  proxy end-to-end (paid)    does the spine's block layout actually produce
                                 cache reads on a real model? — spine_real_ab
  L4  composition (paid)         do rolling memory and delegation compose or
                                 cancel? — combined_2x2, only after L0-L3 green
"""
from __future__ import annotations

import os
import sys
import time

import httpx

API = "https://api.anthropic.com/v1/messages"

# per MTok, published rates (2026-08). cache write (5m) 1.25x input, read 0.1x.
PRICE = {
    "claude-opus-5": {"in": 5.00, "out": 25.00, "min_cache": 512},
    "claude-sonnet-5": {"in": 3.00, "out": 15.00, "min_cache": 1024},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00, "min_cache": 4096},
}
CACHE_WRITE_MULT, CACHE_READ_MULT = 1.25, 0.10


def require_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        raise SystemExit(2)
    return key


def run_nonce() -> str:
    """A fresh nonce per PROCESS. Never key it per-arm: a fixed per-arm nonce
    lets a later run inherit an earlier run's warm cache and silently invalidate
    the comparison — a bug that has bitten this project twice."""
    return str(int(time.time()))


def cost(model: str, u: dict) -> float:
    p = PRICE[model]
    return (
        u.get("input_tokens", 0) * p["in"]
        + u.get("cache_creation_input_tokens", 0) * p["in"] * CACHE_WRITE_MULT
        + u.get("cache_read_input_tokens", 0) * p["in"] * CACHE_READ_MULT
        + u.get("output_tokens", 0) * p["out"]
    ) / 1_000_000


def as_blocks(content) -> list:
    """Normalize any message content to block form. Converting only *some*
    messages flips string↔list across turns and changes the prefix bytes — the
    exact silent invalidator this harness exists to kill. Always normalize all."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [dict(b) for b in content]


def place_breakpoints(messages: list, indices) -> list:
    """Return a copy of messages with cache_control on the last block of each
    listed message index. All messages normalized to block form first."""
    out = [{**m, "content": as_blocks(m["content"])} for m in messages]
    n = len(out)
    for idx in indices:
        if 0 <= idx < n:
            out[idx]["content"][-1] = dict(out[idx]["content"][-1], cache_control={"type": "ephemeral"})
    return out


def call(client: httpx.Client, key: str, model: str, system, messages: list,
         *, tools=None, max_tokens: int = 8, thinking_off: bool = True, effort: str = "low") -> dict:
    """One request → the provider's usage block. thinking/effort pinned so the
    signal under test (context carried) is not drowned by variable thinking."""
    if isinstance(system, str):
        system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if model != "claude-haiku-4-5":  # 4.5 has no thinking/effort surface
        if thinking_off:
            body["thinking"] = {"type": "disabled"}
        body["output_config"] = {"effort": effort}
    if tools is not None:
        body["tools"] = tools
    r = client.post(API, headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                  "content-type": "application/json"}, json=body, timeout=180)
    r.raise_for_status()
    return r.json().get("usage") or {}


class CacheGate:
    """Turn-by-turn cache monitor. Records each turn with its EXPECTED cache
    behavior; a turn that violates the expectation is a hard failure. Prints a
    row every turn so the operator sees the primitive working (or not) live.

    expect values:
      'cold'    first sight of this prefix — read 0 is correct
      'read'    prefix should be warm — read == 0 is a FAILURE
      'rewrite' spine batch boundary — the prefix legitimately grew; read may
                be smaller than last turn but must still be > 0 (the pre-batch
                prefix is still warm) unless it is also a cold segment
      'coldttl' post-TTL resume — read > 0 would mean the cache did NOT lapse,
                which invalidates a cold-resume measurement (inverse assert)
    """

    def __init__(self, label: str, model: str, max_usd: float | None = None):
        self.label, self.model, self.max_usd = label, model, max_usd
        self.rows, self.failures, self.spent = [], [], 0.0

    def record(self, turn: int, usage: dict, expect: str = "read", note: str = "") -> None:
        c = cost(self.model, usage)
        self.spent += c
        rd = usage.get("cache_read_input_tokens", 0)
        wr = usage.get("cache_creation_input_tokens", 0)
        billed = rd + wr + usage.get("input_tokens", 0)
        ok = True
        if expect == "read" and rd == 0:
            ok = False
        elif expect == "coldttl" and rd > 0:
            ok = False
        verdict = {"cold": "frio", "read": "quente" if ok else "*** SEM READ ***",
                   "rewrite": "lote" if rd else "*** lote sem read ***",
                   "coldttl": "TTL lapsou" if ok else "*** TTL NÃO lapsou ***"}[expect]
        if expect == "rewrite" and rd == 0:
            ok = False
        if not ok:
            self.failures.append((turn, expect, rd, wr))
        print(f"  [{self.label}] t{turn:>2} prompt {billed:>7,} read {rd:>7,} write {wr:>6,}"
              f"  {verdict}{('  ' + note) if note else ''}   [${self.spent:.4f}]")
        self.rows.append({"turn": turn, "expect": expect, "ok": ok, "usd": c, **usage})
        if self.max_usd is not None and self.spent > self.max_usd:
            raise SystemExit(f"[{self.label}] aborting: ${self.spent:.4f} > --max-usd {self.max_usd}")

    def passed(self) -> bool:
        return not self.failures

    def assert_ok(self) -> None:
        if self.failures:
            raise SystemExit(f"[{self.label}] CACHE GATE FAILED at turns "
                             f"{[t for t, *_ in self.failures]} — result discarded, not reported.")
