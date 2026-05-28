"""v0.9 real-comparison benchmark.

Runs the same one-shot task via `claude -p` two ways:
  A. default (no agent) — baseline
  B. with `--agent burnless-planner` — burnless mode

Captures usage from stream-json output and, for Burnless mode, also sums nested
`.burnless/logs/d*.log` worker usage created during the run. Writes per-run JSON
+ a Markdown report. Optional `--expect` validates a simple substring in output.

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
    model = {
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-7",
        "haiku": "claude-haiku-4-5-20251001",
    }.get(model, model)
    p = PRICING_PER_MTOK.get(model, PRICING_PER_MTOK["claude-sonnet-4-6"])
    return (tin * p["in"] + tout * p["out"] + cw * p["cache_w_1h"] + cr * p["cache_r"]) / 1_000_000


def _json_line(line: str) -> dict | None:
    line = line.strip()
    if line.startswith("[stdout] "):
        line = line[len("[stdout] "):].strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def parse_stream(stdout: str) -> dict:
    m = {"model": None, "in": 0, "out": 0, "cw": 0, "cr": 0, "session_id": None, "result": ""}
    for line in stdout.splitlines():
        obj = _json_line(line)
        if obj is None:
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

        event = obj.get("event") if obj.get("type") == "stream_event" else None
        if isinstance(event, dict):
            if event.get("type") == "message_start" and not m["model"]:
                msg = event.get("message") or {}
                m["model"] = msg.get("model") or m["model"]
            if event.get("type") == "message_delta":
                u = event.get("usage") or {}
                m["in"] += int(u.get("input_tokens", 0))
                m["out"] += int(u.get("output_tokens", 0))
                m["cw"] += int(u.get("cache_creation_input_tokens", 0))
                m["cr"] += int(u.get("cache_read_input_tokens", 0))
    return m


def collect_worker_usage(cwd: Path, since_ts: float) -> dict:
    log_dir = cwd / ".burnless" / "logs"
    out = {"runs": [], "usd": 0.0, "in": 0, "out": 0, "cw": 0, "cr": 0}
    if not log_dir.is_dir():
        return out

    for log_path in sorted(log_dir.glob("d*.log")):
        try:
            if log_path.stat().st_mtime < since_ts:
                continue
            parsed = parse_stream(log_path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if not any(parsed[k] for k in ("in", "out", "cw", "cr")):
            continue
        model = parsed["model"] or "claude-haiku-4-5-20251001"
        cost = usd(model, parsed["in"], parsed["out"], parsed["cw"], parsed["cr"])
        row = {
            "id": log_path.stem,
            "model": model,
            "usd": cost,
            "in": parsed["in"],
            "out": parsed["out"],
            "cw": parsed["cw"],
            "cr": parsed["cr"],
        }
        out["runs"].append(row)
        out["usd"] += cost
        out["in"] += parsed["in"]
        out["out"] += parsed["out"]
        out["cw"] += parsed["cw"]
        out["cr"] += parsed["cr"]
    return out


def run_one(task: str, agent: str | None, cwd: Path, model: str, expect: str | None) -> dict:
    cmd = [
        "/opt/homebrew/bin/claude", "-p",
        "--model", model,
        "--permission-mode", "bypassPermissions",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    if agent:
        cmd += ["--agent", agent]
    t0 = time.time()
    workers_since = t0
    proc = subprocess.run(cmd, input=task, capture_output=True, text=True, cwd=str(cwd), timeout=600)
    wall = time.time() - t0
    m = parse_stream(proc.stdout)
    workers = collect_worker_usage(cwd, workers_since) if agent else {"runs": [], "usd": 0.0, "in": 0, "out": 0, "cw": 0, "cr": 0}
    m["wall_s"] = wall
    m["exit_code"] = proc.returncode
    m["agent"] = agent or "default"
    m["worker_usage"] = workers
    m["main_usd"] = usd(m["model"] or model, m["in"], m["out"], m["cw"], m["cr"])
    m["usd"] = m["main_usd"] + workers["usd"]
    m["total_in"] = m["in"] + workers["in"]
    m["total_out"] = m["out"] + workers["out"]
    m["total_cw"] = m["cw"] + workers["cw"]
    m["total_cr"] = m["cr"] + workers["cr"]
    m["ok"] = (expect in m["result"]) if expect else None
    return m


def avg(runs: list[dict], key: str) -> float:
    return sum(float(r.get(key, 0) or 0) for r in runs) / len(runs) if runs else 0.0


def render_report(results: dict) -> str:
    lines = [
        "# Sonnet Solo vs Burnless",
        "",
        f"Created: `{results['created_at']}`",
        f"Model: `{results['model']}`",
        f"Runs per condition: `{results['runs_per_condition']}`",
        f"Expect substring: `{results.get('expect') or ''}`",
        "",
        "## Summary",
        "",
        "| Condition | OK | Avg total USD | Main USD | Worker USD | Avg wall | Input | Output | Cache write | Cache read |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, cond in results["conditions"].items():
        ok_values = [r.get("ok") for r in cond["runs"] if r.get("ok") is not None]
        ok_txt = "-" if not ok_values else f"{sum(1 for x in ok_values if x)}/{len(ok_values)}"
        lines.append(
            f"| {name} | {ok_txt} | ${cond['avg_usd']:.6f} | ${cond['avg_main_usd']:.6f} | "
            f"${cond['avg_worker_usd']:.6f} | {cond['avg_wall_s']:.2f}s | "
            f"{cond['avg_total_in']:.1f} | {cond['avg_total_out']:.1f} | "
            f"{cond['avg_total_cw']:.1f} | {cond['avg_total_cr']:.1f} |"
        )
    if "comparison" in results:
        c = results["comparison"]
        lines.extend([
            "",
            "## Comparison",
            "",
            f"- Burnless/default USD ratio: `{c['usd_ratio_burnless_vs_default']:.2f}x`",
            f"- Burnless/default wall ratio: `{c['wall_ratio_burnless_vs_default']:.2f}x`",
            f"- Burnless/default total input ratio: `{c['tokens_in_ratio']:.2f}x`",
        ])
    lines.extend(["", "## Worker Runs", ""])
    for cond_name, cond in results["conditions"].items():
        for idx, run in enumerate(cond["runs"], start=1):
            for worker in run.get("worker_usage", {}).get("runs", []):
                lines.append(
                    f"- `{cond_name}` run {idx}: `{worker['id']}` `{worker['model']}` "
                    f"${worker['usd']:.6f}, in={worker['in']}, out={worker['out']}, "
                    f"cw={worker['cw']}, cr={worker['cr']}"
                )
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True, help="Task prompt to send")
    p.add_argument("--runs", type=int, default=1, help="Runs per condition (averaged)")
    p.add_argument("--label", default=None, help="Optional results subdir label")
    p.add_argument("--cwd", default=None, help="Override cwd (default: repo root)")
    p.add_argument("--model", default="claude-sonnet-4-6", help="Baseline/planner model")
    p.add_argument("--expect", default=None, help="Substring expected in final output")
    args = p.parse_args()

    cwd = Path(args.cwd) if args.cwd else REPO_ROOT
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"_{args.label}" if args.label else "_v09_realcmp"
    out_dir = RESULTS_DIR / f"{ts}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "created_at": ts,
        "task": args.task,
        "model": args.model,
        "expect": args.expect,
        "runs_per_condition": args.runs,
        "conditions": {},
    }
    for cond_name, agent in [("default", None), ("burnless-planner", "burnless-planner")]:
        print(f"\n=== condition: {cond_name} (agent={agent}) ===", file=sys.stderr)
        runs = []
        for i in range(args.runs):
            print(f"  run {i+1}/{args.runs}...", file=sys.stderr)
            r = run_one(args.task, agent, cwd, args.model, args.expect)
            print(
                f"    total_in={r['total_in']} total_out={r['total_out']} "
                f"total_cw={r['total_cw']} total_cr={r['total_cr']} "
                f"main=${r['main_usd']:.4f} workers=${r['worker_usage']['usd']:.4f} "
                f"total=${r['usd']:.4f} wall={r['wall_s']:.1f}s ok={r['ok']}",
                file=sys.stderr,
            )
            runs.append(r)
        results["conditions"][cond_name] = {
            "runs": runs,
            "avg_usd": avg(runs, "usd"),
            "avg_main_usd": avg(runs, "main_usd"),
            "avg_worker_usd": avg([r["worker_usage"] for r in runs], "usd"),
            "avg_wall_s": avg(runs, "wall_s"),
            "avg_in": avg(runs, "in"),
            "avg_out": avg(runs, "out"),
            "avg_cw": avg(runs, "cw"),
            "avg_cr": avg(runs, "cr"),
            "avg_total_in": avg(runs, "total_in"),
            "avg_total_out": avg(runs, "total_out"),
            "avg_total_cw": avg(runs, "total_cw"),
            "avg_total_cr": avg(runs, "total_cr"),
        }

    # Comparison
    d = results["conditions"]
    if "default" in d and "burnless-planner" in d:
        a, b = d["default"], d["burnless-planner"]
        results["comparison"] = {
            "usd_ratio_burnless_vs_default": b["avg_usd"] / a["avg_usd"] if a["avg_usd"] else None,
            "wall_ratio_burnless_vs_default": b["avg_wall_s"] / a["avg_wall_s"] if a["avg_wall_s"] else None,
            "tokens_in_ratio": b["avg_total_in"] / a["avg_total_in"] if a["avg_total_in"] else None,
        }

    out_file = out_dir / "v09_real_compare.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    (out_dir / "report.md").write_text(render_report(results), encoding="utf-8")
    print(f"\n  → {out_file.relative_to(REPO_ROOT)}", file=sys.stderr)
    if "comparison" in results:
        c = results["comparison"]
        print(f"  USD ratio burnless/default: {c.get('usd_ratio_burnless_vs_default'):.2f}x", file=sys.stderr)
        print(f"  Wall ratio burnless/default: {c.get('wall_ratio_burnless_vs_default'):.2f}x", file=sys.stderr)


if __name__ == "__main__":
    main()
