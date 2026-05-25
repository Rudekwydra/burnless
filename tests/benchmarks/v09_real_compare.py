"""v0.9 real-comparison benchmark.

Runs the same one-shot task via `claude -p` two ways:
  A. default (no agent) — baseline
  B. with `--agent burnless-planner` — burnless mode

Captures usage from stream-json output. Writes per-run JSON + comparison
summary. Intended as a SKELETON for v0.9 evaluation. Does NOT validate
correctness of output — only token/cost/wall metrics.

Usage:
  python3 tests/benchmarks/v09_real_compare.py \
    --task "lista os 5 maiores arquivos .py de src/burnless/ por linhas" \
    --runs 1
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = REPO_ROOT / "tests" / "benchmarks" / "results"

PRICING_PER_MTOK = {
    "claude-sonnet-4-6": {"in": 3.00, "out": 15.00, "cache_w_1h": 6.00, "cache_r": 0.30},
    "claude-haiku-4-5-20251001": {"in": 0.80, "out": 4.00, "cache_w_1h": 1.60, "cache_r": 0.08},
    "claude-opus-4-7": {"in": 15.00, "out": 75.00, "cache_w_1h": 30.00, "cache_r": 1.50},
}


def usd(model: str, tin: int, tout: int, cw: int, cr: int) -> float:
    p = PRICING_PER_MTOK.get(model, PRICING_PER_MTOK["claude-sonnet-4-6"])
    return (tin * p["in"] + tout * p["out"] + cw * p["cache_w_1h"] + cr * p["cache_r"]) / 1_000_000


def parse_stream(stdout: str) -> dict:
    m = {"model": None, "in": 0, "out": 0, "cw": 0, "cr": 0, "session_id": None, "result": ""}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "system" and not m["session_id"]:
            m["session_id"] = obj.get("session_id")
            m["model"] = obj.get("model")
        elif obj.get("type") == "result":
            u = obj.get("usage") or {}
            m["in"] += int(u.get("input_tokens", 0))
            m["out"] += int(u.get("output_tokens", 0))
            m["cw"] += int(u.get("cache_creation_input_tokens", 0))
            m["cr"] += int(u.get("cache_read_input_tokens", 0))
            m["result"] = obj.get("result", "") or m["result"]
            if not m["model"]:
                m["model"] = (obj.get("message") or {}).get("model") or "claude-sonnet-4-6"
    return m


def run_one(task: str, agent: str | None, cwd: Path) -> dict:
    cmd = [
        "/opt/homebrew/bin/claude", "-p",
        "--permission-mode", "bypassPermissions",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    if agent:
        cmd += ["--agent", agent]
    t0 = time.time()
    proc = subprocess.run(cmd, input=task, capture_output=True, text=True, cwd=str(cwd), timeout=600)
    wall = time.time() - t0
    m = parse_stream(proc.stdout)
    m["wall_s"] = wall
    m["exit_code"] = proc.returncode
    m["agent"] = agent or "default"
    m["usd"] = usd(m["model"] or "claude-sonnet-4-6", m["in"], m["out"], m["cw"], m["cr"])
    return m


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True, help="Task prompt to send")
    p.add_argument("--runs", type=int, default=1, help="Runs per condition (averaged)")
    p.add_argument("--label", default=None, help="Optional results subdir label")
    p.add_argument("--cwd", default=None, help="Override cwd (default: repo root)")
    args = p.parse_args()

    cwd = Path(args.cwd) if args.cwd else REPO_ROOT
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"_{args.label}" if args.label else "_v09_realcmp"
    out_dir = RESULTS_DIR / f"{ts}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {"task": args.task, "runs_per_condition": args.runs, "conditions": {}}
    for cond_name, agent in [("default", None), ("burnless-planner", "burnless-planner")]:
        print(f"\n=== condition: {cond_name} (agent={agent}) ===", file=sys.stderr)
        runs = []
        for i in range(args.runs):
            print(f"  run {i+1}/{args.runs}...", file=sys.stderr)
            r = run_one(args.task, agent, cwd)
            print(f"    in={r['in']} out={r['out']} cw={r['cw']} cr={r['cr']} usd={r['usd']:.4f} wall={r['wall_s']:.1f}s", file=sys.stderr)
            runs.append(r)
        results["conditions"][cond_name] = {
            "runs": runs,
            "avg_usd": sum(r["usd"] for r in runs) / len(runs),
            "avg_wall_s": sum(r["wall_s"] for r in runs) / len(runs),
            "avg_in": sum(r["in"] for r in runs) / len(runs),
            "avg_out": sum(r["out"] for r in runs) / len(runs),
            "avg_cw": sum(r["cw"] for r in runs) / len(runs),
            "avg_cr": sum(r["cr"] for r in runs) / len(runs),
        }

    # Comparison
    d = results["conditions"]
    if "default" in d and "burnless-planner" in d:
        a, b = d["default"], d["burnless-planner"]
        results["comparison"] = {
            "usd_ratio_burnless_vs_default": b["avg_usd"] / a["avg_usd"] if a["avg_usd"] else None,
            "wall_ratio_burnless_vs_default": b["avg_wall_s"] / a["avg_wall_s"] if a["avg_wall_s"] else None,
            "tokens_in_ratio": b["avg_in"] / a["avg_in"] if a["avg_in"] else None,
        }

    out_file = out_dir / "v09_real_compare.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\n  → {out_file.relative_to(REPO_ROOT)}", file=sys.stderr)
    if "comparison" in results:
        c = results["comparison"]
        print(f"  USD ratio burnless/default: {c.get('usd_ratio_burnless_vs_default'):.2f}x", file=sys.stderr)
        print(f"  Wall ratio burnless/default: {c.get('wall_ratio_burnless_vs_default'):.2f}x", file=sys.stderr)


if __name__ == "__main__":
    main()
