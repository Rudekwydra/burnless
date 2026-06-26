"""Benchmarks four real no-proxy Burnless flow scenarios.

Measures real UX via `claude -p` (raw, observe mode, burnless-planner agent) and
`burnless do` CLI. Every claim must state its exact scenario; see CAVEATS and --dry-run.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path as _Path

_sys = sys  # alias for monkeypatch in tests
try:
    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from v09_real_compare import parse_stream, usd, collect_worker_usage
except ImportError:
    parse_stream = None
    usd = None
    collect_worker_usage = None

SCENARIOS = [
    {
        "name": "raw",
        "label": "Claude Code raw session",
        "mode": "claude_p",
        "agent": None,
        "burnless_mode": None,
        "invocation": "claude -p --output-format stream-json <task>",
    },
    {
        "name": "observe",
        "label": "Claude Code + Burnless observe",
        "mode": "claude_p",
        "agent": None,
        "burnless_mode": "observe",
        "invocation": "BURNLESS_MODE=observe claude -p --output-format stream-json <task>",
    },
    {
        "name": "on",
        "label": "Claude Code + Burnless on",
        "mode": "claude_p",
        "agent": "burnless-planner",
        "burnless_mode": "on",
        "invocation": "claude -p --agent burnless-planner --output-format stream-json <task>",
    },
    {
        "name": "cli_do",
        "label": "Burnless CLI do with worker routing",
        "mode": "cli_do",
        "agent": None,
        "burnless_mode": None,
        "invocation": "burnless do --tier silver <task>",
    },
]

METRICS = [
    "input_tokens",
    "output_tokens",
    "cache_read",
    "cache_write",
    "assistant_turns",
    "worker_delegations",
    "retrieval_calls",
    "verify_pass_fail",
    "wall_time",
    "successful_completion",
    "user_visible_verbosity",
    "post_clear_recovery",
]

CAVEATS = [
    "Each row states its exact scenario; numbers are not comparable across scenarios without the caveat.",
    "Live runs invoke real `claude -p` / `burnless do`; cost and latency depend on model and machine.",
    "Dry-run performs NO model calls and reports only the measurement plan.",
]


def build_plan(task, runs=1):
    """Return the measurement plan for all scenarios. Pure; safe for --dry-run.

    Shape: {"task": task, "runs": runs, "metrics": METRICS,
            "caveats": CAVEATS,
            "scenarios": [ {name, label, mode, agent, burnless_mode,
                            invocation, metrics: METRICS} for each scenario ]}.
    """
    return {
        "task": task,
        "runs": runs,
        "metrics": list(METRICS),
        "caveats": list(CAVEATS),
        "scenarios": [
            {
                **dict(s),
                "invocation": s["invocation"].replace("<task>", repr(task)),
                "metrics": list(METRICS),
            }
            for s in SCENARIOS
        ],
    }


def run_scenario(scenario, task, cwd, model="claude-sonnet-4-6"):
    """Execute one scenario for real and return a metrics dict.

    Uses subprocess + parse_stream/usd/collect_worker_usage reused from
    v09_real_compare. For mode == 'cli_do', shells out to `burnless do`.
    Returns a dict keyed by the METRICS names plus raw token fields.
    Never raises on a failed run: capture exit_code and set
    successful_completion accordingly.
    """
    if parse_stream is None or usd is None or collect_worker_usage is None:
        return {m: None for m in METRICS}

    result = {m: None for m in METRICS}
    result["wall_time"] = 0.0
    result["successful_completion"] = False
    result["assistant_turns"] = 0
    result["worker_delegations"] = 0
    result["verify_pass_fail"] = None

    try:
        mode = scenario["mode"]
        t0 = time.time()
        workers_since = t0

        if mode == "claude_p":
            cmd = [
                "claude",
                "-p",
                "--model",
                model,
                "--permission-mode",
                "bypassPermissions",
                "--output-format",
                "stream-json",
                "--verbose",
                "--include-partial-messages",
            ]
            if scenario["agent"]:
                cmd += ["--agent", scenario["agent"]]

            env = None
            if scenario["burnless_mode"]:
                import os
                env = os.environ.copy()
                env["BURNLESS_MODE"] = scenario["burnless_mode"]

            proc = subprocess.run(
                cmd,
                input=task,
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=600,
                env=env,
            )
            wall = time.time() - t0

            parsed = parse_stream(proc.stdout)
            result["input_tokens"] = parsed.get("in", 0)
            result["output_tokens"] = parsed.get("out", 0)
            result["cache_read"] = parsed.get("cr", 0)
            result["cache_write"] = parsed.get("cw", 0)
            result["wall_time"] = wall
            result["successful_completion"] = proc.returncode == 0

            assistant_turn_count = 0
            for line in proc.stdout.splitlines():
                try:
                    obj = json.loads(line.strip())
                    if isinstance(obj, dict):
                        event = obj.get("event")
                        if isinstance(event, dict) and event.get("type") == "message_delta":
                            assistant_turn_count += 1
                except (json.JSONDecodeError, AttributeError):
                    pass
            result["assistant_turns"] = assistant_turn_count

            workers = collect_worker_usage(_Path(cwd), workers_since) if scenario["agent"] else None
            if workers:
                result["worker_delegations"] = len(workers.get("runs", []))

        elif mode == "cli_do":
            proc = subprocess.run(
                ["burnless", "do", "--tier", "silver", task],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=600,
            )
            wall = time.time() - t0

            result["input_tokens"] = 0
            result["output_tokens"] = 0
            result["cache_read"] = 0
            result["cache_write"] = 0
            result["wall_time"] = wall
            result["successful_completion"] = proc.returncode == 0

            workers = collect_worker_usage(_Path(cwd), workers_since) if collect_worker_usage else None
            if workers:
                result["worker_delegations"] = len(workers.get("runs", []))

    except subprocess.TimeoutExpired:
        result["wall_time"] = time.time() - t0
        result["successful_completion"] = False
    except Exception:
        pass

    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--task",
        default="List the 5 largest .py files in src/burnless by line count.",
        help="Task prompt",
    )
    p.add_argument("--runs", type=int, default=1, help="Number of runs per scenario")
    p.add_argument("--dry-run", action="store_true", help="Print plan only (no subprocess)")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument("--model", default="claude-sonnet-4-6", help="Model to use")
    args = p.parse_args()

    if args.dry_run:
        plan = build_plan(args.task, args.runs)
        if args.json:
            print(json.dumps(plan, indent=2))
        else:
            print(f"Task: {args.task}")
            print(f"Runs per scenario: {args.runs}")
            print()
            print("Scenarios:")
            for s in plan["scenarios"]:
                print(f"  - {s['name']:10} {s['label']}")
                print(f"    {s['invocation']}")
            print()
            print("Metrics:")
            for m in plan["metrics"]:
                print(f"  - {m}")
            print()
            print("Caveats:")
            for c in plan["caveats"]:
                print(f"  - {c}")
        return 0

    cwd = _Path.cwd()
    results = []
    for scenario in SCENARIOS:
        for run_idx in range(args.runs):
            row = {"scenario": scenario["name"], "run": run_idx + 1}
            metrics = run_scenario(scenario, args.task, cwd, args.model)
            row.update(metrics)
            results.append(row)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for row in results:
            print(row)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
