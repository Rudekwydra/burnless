"""Read the spine proxy ledger and report what actually happened.

Two row kinds are written by the server: `request` (what the transform did
to the outgoing body) and `usage` (what the provider itself reported —
input, output, and crucially cache_read vs cache_creation). The synthetic
benchmark says how much smaller the request got; only the usage rows say
how much of it the provider billed as a cache read. This module aggregates
both so a day of real sessions produces a number nobody has to trust on
faith.

Reading is fail-open: a truncated or hand-edited ledger yields the rows it
can parse.
"""
from __future__ import annotations

import json
from pathlib import Path


def read_rows(ledger: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(ledger, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def summarize(ledger: Path) -> dict:
    """Aggregate a ledger into the numbers worth reporting."""
    rows = read_rows(ledger)
    requests = [r for r in rows if r.get("kind", "request") == "request"]
    usages = [r for r in rows if r.get("kind") == "usage"]

    applied = [r for r in requests if r.get("applied")]
    reasons: dict[str, int] = {}
    for r in requests:
        if not r.get("applied"):
            reasons[str(r.get("reason", "?"))] = reasons.get(str(r.get("reason", "?")), 0) + 1

    def total(rows_: list[dict], key: str) -> int:
        return sum(int(r.get(key) or 0) for r in rows_)

    cache_read = total(usages, "cache_read_input_tokens")
    cache_write = total(usages, "cache_creation_input_tokens")
    fresh_input = total(usages, "input_tokens")
    billed_prompt = cache_read + cache_write + fresh_input

    return {
        "requests": len(requests),
        "spine_applied": len(applied),
        "passthrough_reasons": reasons,
        "max_absorbed": max((int(r.get("absorbed") or 0) for r in applied), default=0),
        "tokens_saved_approx": total(applied, "tokens_saved_approx"),
        "bytes_in": total(requests, "bytes_in"),
        "bytes_out": total(applied, "bytes_out"),
        "usage_rows": len(usages),
        "input_tokens": fresh_input,
        "output_tokens": total(usages, "output_tokens"),
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
        "billed_prompt_tokens": billed_prompt,
        # share of prompt tokens the provider served from cache — the number
        # that proves the append-only spine keeps the prefix warm
        "cache_hit_ratio": (cache_read / billed_prompt) if billed_prompt else None,
    }


def render(summary: dict) -> str:
    lines = [
        "burnless spine proxy — real session ledger",
        "",
        f"  requests seen        {summary['requests']}",
        f"  spine applied        {summary['spine_applied']}"
        + (f"  (deepest spine: {summary['max_absorbed']} capsules)" if summary["max_absorbed"] else ""),
    ]
    if summary["passthrough_reasons"]:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(summary["passthrough_reasons"].items()))
        lines.append(f"  passthrough          {detail}")
    if summary["tokens_saved_approx"]:
        lines.append(f"  tokens not sent      {summary['tokens_saved_approx']:,} (approx, spine substitution)")
    if summary["bytes_in"]:
        lines.append(f"  request bytes        {summary['bytes_in']:,} in → {summary['bytes_out']:,} out (applied only)")

    if not summary["usage_rows"]:
        lines += ["", "  no provider usage recorded yet — run real traffic through the proxy"]
        return "\n".join(lines)

    ratio = summary["cache_hit_ratio"]
    lines += [
        "",
        f"  provider usage over {summary['usage_rows']} responses:",
        f"    prompt billed      {summary['billed_prompt_tokens']:,} tokens",
        f"    cache read         {summary['cache_read_input_tokens']:,}"
        + (f"  ({ratio:.1%} of prompt — warm prefix)" if ratio is not None else ""),
        f"    cache written      {summary['cache_creation_input_tokens']:,}",
        f"    fresh input        {summary['input_tokens']:,}",
        f"    output             {summary['output_tokens']:,}",
    ]
    return "\n".join(lines)
