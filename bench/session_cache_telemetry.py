#!/usr/bin/env python3
"""Real-session prompt-cache telemetry for a Claude CLI (maestro) session.

Reads a Claude Code session JSONL and computes, from the actual `message.usage`
fields, what the input cost was WITH the hot prefix cache vs the counterfactuals:
  - no-cache  (every input token billed at 1x)
  - thrash    (the whole prefix re-billed as cache_write 1.25x every turn)

This is the honest "what the cache bought" contrast. NOTE: prompt caching itself is
provided by the platform (Claude Code). Burnless's contribution is keeping the cache
HOT — a byte-stable prefix with volatile content only at the tail — so you land on the
`hot` column and never on `thrash`. A tiny cache_create share is the proof the prefix
is never rewritten.

Usage:
  python3 bench/session_cache_telemetry.py [--jsonl PATH]
  (default: most recent *.jsonl under ~/.claude/projects/*/)
"""
from __future__ import annotations
import argparse, json, glob, os, sys

# Anthropic price multipliers relative to base input (=1.0):
CACHE_READ = 0.1     # cached prefix read
CACHE_WRITE = 1.25   # cache creation (write)
FRESH = 1.0          # uncached fresh input


def latest_jsonl() -> str | None:
    pats = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    return max(pats, key=os.path.getmtime) if pats else None


def analyze(path: str) -> dict:
    cr = cc = fr = out = turns = 0
    for ln in open(path):
        try:
            o = json.loads(ln)
        except Exception:
            continue
        m = o.get("message", {})
        if o.get("type") == "assistant" and isinstance(m, dict):
            u = m.get("usage") or {}
            if not u:
                continue
            cr += u.get("cache_read_input_tokens", 0)
            cc += u.get("cache_creation_input_tokens", 0)
            fr += u.get("input_tokens", 0)
            out += u.get("output_tokens", 0)
            turns += 1
    total_in = cr + cc + fr
    hot = cr * CACHE_READ + cc * CACHE_WRITE + fr * FRESH
    nocache = total_in * FRESH
    thrash = (cr + cc) * CACHE_WRITE + fr * FRESH
    return {
        "turns": turns, "cache_read": cr, "cache_create": cc, "fresh": fr,
        "output": out, "total_input": total_in,
        "hot_units": hot, "nocache_units": nocache, "thrash_units": thrash,
        "nocache_x": (nocache / hot) if hot else 0,
        "thrash_x": (thrash / hot) if hot else 0,
        "create_share_pct": (cc / total_in * 100) if total_in else 0,
        "hot_pct_of_nocache": (hot / nocache * 100) if nocache else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default=None)
    a = ap.parse_args()
    path = a.jsonl or latest_jsonl()
    if not path or not os.path.exists(path):
        print("no session JSONL found", file=sys.stderr)
        return 1
    r = analyze(path)
    print(f"session: {os.path.basename(path)}  ({r['turns']} assistant turns)")
    print(f"  input total: {r['total_input']:,}  (cache_read={r['cache_read']:,} | "
          f"cache_create={r['cache_create']:,} | fresh={r['fresh']:,})  output={r['output']:,}")
    print(f"  cache_create share of input: {r['create_share_pct']:.1f}%  (low = prefix never rewritten = HOT)")
    print(f"  input cost units:  hot={r['hot_units']:,.0f}  no-cache={r['nocache_units']:,.0f}  thrash={r['thrash_units']:,.0f}")
    print(f"  no-cache is {r['nocache_x']:.1f}x the hot cost;  thrash is {r['thrash_x']:.1f}x the hot cost")
    print(f"  hot paid {r['hot_pct_of_nocache']:.1f}% of the no-cache equivalent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
