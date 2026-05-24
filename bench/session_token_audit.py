#!/usr/bin/env python3
"""
Session token audit: parse Claude Code session JSONL transcripts and report token economics.
"""

import json
import os
import sys
import glob
import argparse
from pathlib import Path

# Opus pricing per 1M tokens (USD). ADJUST to current Anthropic rates.
BASE_INPUT_PER_M = 15.0
BASE_OUTPUT_PER_M = 75.0
CACHE_READ_MULT = 0.10  # cache read = 0.1x base input
CACHE_1H_MULT = 2.00    # 1h cache write = 2x base input
CACHE_5M_MULT = 1.25    # 5m cache write = 1.25x base input


def resolve_path(session, project_dir):
    """Resolve session path from various input formats."""
    if session is None:
        # Find most recently modified .jsonl in project_dir
        files = glob.glob(os.path.join(project_dir, "*.jsonl"))
        if not files:
            print(f"Error: no .jsonl files found in {project_dir}", file=sys.stderr)
            sys.exit(1)
        return max(files, key=os.path.getmtime)

    # Direct file path
    if os.path.isfile(session):
        return session

    # Try appending .jsonl
    if os.path.isfile(session + ".jsonl"):
        return session + ".jsonl"

    # Try in project_dir
    candidate = os.path.join(
        project_dir,
        session if session.endswith(".jsonl") else session + ".jsonl"
    )
    if os.path.isfile(candidate):
        return candidate

    print(f"Error: session file not found: {session}", file=sys.stderr)
    sys.exit(1)


def parse_session(filepath):
    """Parse JSONL session file and extract token/model data."""
    turns = []
    models = set()
    seen_ids = set()
    total_records = 0
    dup_records = 0

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue

            u = msg.get("usage")
            if not isinstance(u, dict):
                continue

            # Count all qualifying records
            total_records += 1

            # Get message id for deduplication
            mid = msg.get("id")
            if mid is not None and mid in seen_ids:
                # Skip duplicate message
                dup_records += 1
                continue
            if mid is not None:
                seen_ids.add(mid)

            # Extract fields
            inp = u.get("input_tokens", 0)
            cread = u.get("cache_read_input_tokens", 0)
            ccreate = u.get("cache_creation_input_tokens", 0)
            out = u.get("output_tokens", 0)

            cc = u.get("cache_creation") or {}
            c1h = cc.get("ephemeral_1h_input_tokens", 0)
            c5m = cc.get("ephemeral_5m_input_tokens", 0)

            model = msg.get("model", "?")

            turns.append({
                "input": inp,
                "cache_read": cread,
                "cache_create": ccreate,
                "output": out,
                "cache_1h": c1h,
                "cache_5m": c5m,
                "model": model,
            })

            if model != "?":
                models.add(model)

    return turns, sorted(list(models)), total_records, dup_records


def calculate_aggregates(turns, total_records, dup_records):
    """Calculate aggregates and cache statistics."""
    sum_input = sum(t["input"] for t in turns)
    sum_cache_read = sum(t["cache_read"] for t in turns)
    sum_cache_create = sum(t["cache_create"] for t in turns)
    sum_out = sum(t["output"] for t in turns)
    sum_c1h = sum(t["cache_1h"] for t in turns)
    sum_c5m = sum(t["cache_5m"] for t in turns)

    total_input_side = sum_input + sum_cache_read + sum_cache_create

    if total_input_side > 0:
        cache_hit_ratio = sum_cache_read / total_input_side
    else:
        cache_hit_ratio = 0.0

    # Cost estimation
    cost = (
        (sum_input / 1e6) * BASE_INPUT_PER_M
        + (sum_cache_read / 1e6) * BASE_INPUT_PER_M * CACHE_READ_MULT
        + (sum_c1h / 1e6) * BASE_INPUT_PER_M * CACHE_1H_MULT
        + (sum_c5m / 1e6) * BASE_INPUT_PER_M * CACHE_5M_MULT
        + (sum_out / 1e6) * BASE_OUTPUT_PER_M
    )

    # Fallback: if no specific 1h/5m but has cache_create, price as 1h
    if (sum_c1h + sum_c5m) == 0 and sum_cache_create > 0:
        cost = (
            (sum_input / 1e6) * BASE_INPUT_PER_M
            + (sum_cache_read / 1e6) * BASE_INPUT_PER_M * CACHE_READ_MULT
            + (sum_cache_create / 1e6) * BASE_INPUT_PER_M * CACHE_1H_MULT
            + (sum_out / 1e6) * BASE_OUTPUT_PER_M
        )

    return {
        "turns": len(turns),
        "sum_input": sum_input,
        "sum_cache_read": sum_cache_read,
        "sum_cache_create": sum_cache_create,
        "sum_out": sum_out,
        "sum_c1h": sum_c1h,
        "sum_c5m": sum_c5m,
        "total_input_side": total_input_side,
        "cache_hit_ratio": cache_hit_ratio,
        "cost": cost,
        "total_records": total_records,
        "dup_records": dup_records,
    }


def format_human_output(agg, models, session_name):
    """Format summary as human-readable table."""
    models_str = ", ".join(models) if models else "?"

    lines = [
        f"=== session token audit: {session_name} ===",
        f"turns (assistant):        {agg['turns']}",
        f"unique msgs / records:    {agg['turns']} / {agg['total_records']}",
        f"model(s):                 {models_str}",
        f"uncached input:           {agg['sum_input']:>11,}  tok",
        f"cache read:               {agg['sum_cache_read']:>11,}  tok",
        f"cache create (1h):        {agg['sum_c1h']:>11,}  tok",
        f"cache create (5m):        {agg['sum_c5m']:>11,}  tok",
        f"output:                   {agg['sum_out']:>11,}  tok",
        f"------------------------------------------",
        f"total input-side:         {agg['total_input_side']:>11,}  tok",
        f"cache hit ratio:          {agg['cache_hit_ratio']*100:>11.1f}%",
        f"est. cost (verify rates): ${agg['cost']:>10.2f}",
    ]

    return "\n".join(lines)


def format_verbose_table(turns):
    """Format per-turn table."""
    lines = ["#  | input | cache_read | cache_create | output"]
    lines.append("-" * 60)

    for i, turn in enumerate(turns, 1):
        inp = turn["input"]
        cread = turn["cache_read"]
        ccreate = turn["cache_create"]
        out = turn["output"]

        lines.append(
            f"{i:>3} | {inp:>10,} | {cread:>13,} | {ccreate:>14,} | {out:>10,}"
        )

    return "\n".join(lines)


def format_json_output(agg, models, session_name):
    """Format summary as JSON."""
    summary_dict = {
        "session": session_name,
        "turns": agg["turns"],
        "total_records": agg["total_records"],
        "dup_records": agg["dup_records"],
        "models": models,
        "uncached_input": agg["sum_input"],
        "cache_read": agg["sum_cache_read"],
        "cache_create_1h": agg["sum_c1h"],
        "cache_create_5m": agg["sum_c5m"],
        "output": agg["sum_out"],
        "total_input_side": agg["total_input_side"],
        "cache_hit_ratio": round(agg["cache_hit_ratio"], 4),
        "est_cost_usd": round(agg["cost"], 2),
    }

    return json.dumps(summary_dict, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Audit Claude Code session token economics.")
    parser.add_argument(
        "session",
        nargs="?",
        default=None,
        help="Path to .jsonl transcript or bare session-id (optional)",
    )
    parser.add_argument(
        "--project-dir",
        default=os.path.expanduser("~/.claude/projects/-Users-roberto-antigravity-burnless"),
        help="Directory holding transcripts",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print per-turn table",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit summary as JSON",
    )

    args = parser.parse_args()

    # Resolve session path
    filepath = resolve_path(args.session, args.project_dir)
    session_name = Path(filepath).stem

    # Parse JSONL
    turns, models, total_records, dup_records = parse_session(filepath)

    if not turns:
        print(f"Warning: no assistant turns found in {filepath}", file=sys.stderr)
        agg = {
            "turns": 0,
            "sum_input": 0,
            "sum_cache_read": 0,
            "sum_cache_create": 0,
            "sum_out": 0,
            "sum_c1h": 0,
            "sum_c5m": 0,
            "total_input_side": 0,
            "cache_hit_ratio": 0.0,
            "cost": 0.0,
            "total_records": total_records,
            "dup_records": dup_records,
        }
    else:
        agg = calculate_aggregates(turns, total_records, dup_records)

    # Output
    if args.json:
        print(format_json_output(agg, models, session_name))
    else:
        if args.verbose:
            print(format_verbose_table(turns))
            print()

        print(format_human_output(agg, models, session_name))


if __name__ == "__main__":
    main()
