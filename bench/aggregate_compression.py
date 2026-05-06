"""Aggregate compression filter results across model runs.

Reads OVERALL lines from log files (or JSON saves in ~/.burnless/test_data/filter_runs/),
prints a comparison table + ASCII chart, and writes a summary JSON.

Usage:
    python bench/aggregate_compression.py /tmp/run_*.log
    python bench/aggregate_compression.py --json   # read from saved JSON files instead
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

OVERALL_RE = re.compile(
    r"OVERALL: orig=(?P<orig>\d+)t -> final=(?P<final>\d+)t \((?P<ratio>[\d.]+)x\)\s+savings=\$(?P<savings>[\d.]+)"
)
MODEL_RE = re.compile(r"using (?P<model>\S+) via")


def parse_log(path: Path) -> dict | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    overall = OVERALL_RE.search(text)
    model = MODEL_RE.search(text)
    if not overall:
        return None
    return {
        "model": model.group("model") if model else path.stem,
        "orig_tokens": int(overall.group("orig")),
        "final_tokens": int(overall.group("final")),
        "ratio": float(overall.group("ratio")),
        "savings_usd": float(overall.group("savings")),
        "source": str(path),
    }


def ascii_bar(value: float, max_value: float, width: int = 40) -> str:
    if max_value <= 0:
        return ""
    filled = int(round(width * value / max_value))
    return "█" * filled + "░" * (width - filled)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="*", default=[], help="log files; defaults to /tmp/run_*.log")
    args = ap.parse_args()

    paths = [Path(p) for p in args.logs] if args.logs else [Path(p) for p in sorted(glob.glob("/tmp/run_*.log"))]
    results = [r for r in (parse_log(p) for p in paths) if r is not None]

    if not results:
        print("No OVERALL lines found in logs.", file=sys.stderr)
        return 1

    # Sort by ratio desc (most aggressive first)
    results.sort(key=lambda r: r["ratio"], reverse=True)

    print()
    print(f"{'model':<32} {'orig':>6} {'final':>6} {'ratio':>7}  bar (compression ratio)")
    print("-" * 95)
    max_r = max(r["ratio"] for r in results)
    for r in results:
        print(f"  {r['model']:<30} {r['orig_tokens']:>6} {r['final_tokens']:>6} {r['ratio']:>6.2f}x  {ascii_bar(r['ratio'], max_r)}")

    print()
    print("Note: ratio = original_tokens / final_tokens (higher = more compression).")
    print("Same 50 PT samples across all models. Tokens via tiktoken cl100k_base.")
    print()

    # Save aggregated JSON
    out = Path.home() / ".burnless" / "test_data" / "filter_runs"
    out.mkdir(parents=True, exist_ok=True)
    out_file = out / "aggregate.json"
    out_file.write_text(json.dumps({"results": results}, indent=2))
    print(f"saved: {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
