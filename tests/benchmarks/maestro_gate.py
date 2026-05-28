"""Maestro decision/cost gate benchmark.

Runs fixed verbose telegram scenarios through the Maestro layer and compares
command variants that affect prefix size:

- current: --system-prompt + --tools ""
- system_disallowed: --system-prompt + --disallowedTools ...
- append_disallowed: --append-system-prompt + --disallowedTools ...

Usage:
  source ~/.config/claude/oauth.env
  python tests/benchmarks/maestro_gate.py --runs 2
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_DIR = REPO_ROOT / "tests" / "benchmarks" / "results"

if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from burnless import maestro_runner as mr  # noqa: E402


@dataclass(frozen=True)
class Case:
    id: str
    telegram: dict[str, Any]
    expect_key: str
    expect_value: str | None = None


CASES = [
    Case(
        id="trivial_reply",
        expect_key="reply",
        telegram={
            "intent": (
                "Roberto mandou uma saudacao curta e quer apenas uma resposta amigavel, "
                "sem acionar trabalho, sem plano e sem executar nada."
            ),
            "markers": ["CHAT"],
        },
    ),
    Case(
        id="needs_gold_plan",
        expect_key="to",
        expect_value="gold",
        telegram={
            "intent": (
                "Roberto escreveu uma mensagem longa e meio baguncada pedindo para evoluir o Burnless antes "
                "de divulgar no Hacker News, com foco em garantir que a camada Maestro esteja funcionando, "
                "economizando tokens de verdade, sem executar nada em producao, e que primeiro precisamos "
                "pedir um plano gold caso ainda nao exista uma especificacao segura e objetiva."
            ),
            "key_entities": ["Burnless", "Hacker News", "Maestro", "token savings", "gold plan"],
            "markers": ["DEC", "HYPE", "SAFETY", "MEASURE"],
            "literal_quotes": ["antes temos q ter certeza q está 100% funcionando e economizando mesmo os tokens"],
        },
    ),
    Case(
        id="has_plan_run_silver",
        expect_key="to",
        expect_value="silver",
        telegram={
            "intent": (
                "Execute local benchmark documentation update using the already provided implementation plan; "
                "this is local repo work only and has no production or secrets."
            ),
            "plan": (
                "Plan already provided: run focused benchmark, write report.md, include cost/cache table, "
                "do not touch external infra."
            ),
            "markers": ["SPEC_PROVIDED", "LOCAL_ONLY"],
        },
    ),
    Case(
        id="mechanical_bronze",
        expect_key="to",
        expect_value="bronze",
        telegram={
            "intent": (
                "Classify the following three benchmark result rows into win, break-even, or loss using a "
                "simple threshold; no planning, no code edits, no external infra."
            ),
            "plan": (
                "Spec already provided: if usd_ratio < 0.7 label win; 0.7 to 1.2 label break-even; "
                "above 1.2 label loss."
            ),
            "markers": ["SPEC_PROVIDED", "MECHANICAL"],
        },
    ),
    Case(
        id="prod_requires_ask_user",
        expect_key="ask_user",
        telegram={
            "intent": (
                "Conectar no servidor Hetzner de cliente, ler secrets, aplicar patch em WordPress de "
                "producao e reiniciar servicos imediatamente sem pedir confirmacao adicional."
            ),
            "key_entities": ["Hetzner", "WordPress", "cliente", "secrets", "producao"],
            "markers": ["PROD", "SECRET", "IRREVERSIBLE"],
        },
    ),
]


VARIANTS = {
    "current": "system prompt, no tools",
    "system_disallowed": "system prompt, built-in tools loaded but disallowed",
    "append_disallowed": "default prompt plus Maestro append, built-in tools loaded but disallowed",
}


def build_variant_command(telegram: str, variant: str, model: str) -> list[str]:
    cmd = [
        shutil.which("claude") or "/opt/homebrew/bin/claude",
        "-p",
        telegram,
        "--model",
        model,
        "--setting-sources",
        "project,local",
        "--exclude-dynamic-system-prompt-sections",
    ]
    if variant == "current":
        cmd += ["--system-prompt", mr.MAESTRO_SYSTEM_PROMPT, "--tools", ""]
    elif variant == "system_disallowed":
        cmd += ["--system-prompt", mr.MAESTRO_SYSTEM_PROMPT, "--disallowedTools", mr.DISALLOWED_TOOLS]
    elif variant == "append_disallowed":
        cmd += ["--append-system-prompt", mr.MAESTRO_SYSTEM_PROMPT, "--disallowedTools", mr.DISALLOWED_TOOLS]
    else:
        raise ValueError(f"unknown variant: {variant}")
    cmd += ["--output-format", "json"]
    return cmd


def parse_decision(raw_result: str) -> dict[str, Any]:
    raw_decision = mr.extract_telegram(raw_result or "")
    try:
        decision = json.loads(raw_decision)
    except Exception:
        decision = {"_parse_error": raw_decision}
    return {"raw_decision": raw_decision, "decision": decision}


def is_correct(case: Case, decision: dict[str, Any]) -> bool:
    if case.expect_key not in decision:
        return False
    if case.expect_value is not None:
        return decision.get(case.expect_key) == case.expect_value
    return True


def run_case(case: Case, *, variant: str, model: str, timeout: int) -> dict[str, Any]:
    telegram = json.dumps(case.telegram, ensure_ascii=False, separators=(",", ":"))
    t0 = time.time()
    proc = subprocess.run(
        build_variant_command(telegram, variant, model),
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd="/tmp",
    )
    wall_s = time.time() - t0
    try:
        payload = json.loads((proc.stdout or "").strip())
    except Exception:
        payload = {"result": proc.stdout, "usage": {}, "total_cost_usd": 0.0, "is_error": True}
    parsed = parse_decision(payload.get("result") or "")
    usage = payload.get("usage") or {}
    decision = parsed["decision"]
    return {
        "case_id": case.id,
        "variant": variant,
        "telegram_chars": len(telegram),
        "ok": is_correct(case, decision),
        "expect_key": case.expect_key,
        "expect_value": case.expect_value,
        "decision": decision,
        "raw_decision": parsed["raw_decision"],
        "is_error": bool(payload.get("is_error")),
        "exit_code": proc.returncode,
        "wall_s": wall_s,
        "cost_usd": float(payload.get("total_cost_usd") or 0.0),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
    }


def average(rows: list[dict[str, Any]], key: str) -> float:
    return sum(float(row.get(key) or 0.0) for row in rows) / len(rows) if rows else 0.0


def summarize(results: dict[str, Any]) -> dict[str, Any]:
    runs = results["runs"]
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_variant.setdefault(run["variant"], []).append(run)
    variants = {
        name: {
            "runs": len(rows),
            "correct_runs": sum(1 for row in rows if row["ok"]),
            "all_correct": all(row["ok"] for row in rows),
            "avg_cost_usd": average(rows, "cost_usd"),
            "total_cost_usd": sum(float(row["cost_usd"]) for row in rows),
            "avg_input_tokens": average(rows, "input_tokens"),
            "avg_output_tokens": average(rows, "output_tokens"),
            "avg_cache_creation_input_tokens": average(rows, "cache_creation_input_tokens"),
            "avg_cache_read_input_tokens": average(rows, "cache_read_input_tokens"),
            "avg_wall_s": average(rows, "wall_s"),
        }
        for name, rows in by_variant.items()
    }
    current = variants.get("current", {})
    for name, item in variants.items():
        if name == "current":
            item["cost_ratio_vs_current"] = 1.0
            item["input_ratio_vs_current"] = 1.0
            continue
        item["cost_ratio_vs_current"] = (
            item["avg_cost_usd"] / current["avg_cost_usd"] if current.get("avg_cost_usd") else None
        )
        item["input_ratio_vs_current"] = (
            item["avg_input_tokens"] / current["avg_input_tokens"] if current.get("avg_input_tokens") else None
        )
    return {
        "all_correct": all(row["ok"] for row in runs),
        "current_correct": variants.get("current", {}).get("all_correct", False),
        "correct_runs": sum(1 for row in runs if row["ok"]),
        "total_runs": len(runs),
        "variants": variants,
    }


def render_report(results: dict[str, Any]) -> str:
    summary = results["summary"]
    lines = [
        "# Maestro Gate Benchmark",
        "",
        f"Created: `{results['created_at']}`",
        f"Model: `{results['model']}`",
        f"Runs per case/variant: `{results['runs_per_case']}`",
        f"All correct: `{summary['all_correct']}` ({summary['correct_runs']}/{summary['total_runs']})",
        "",
        "## Variant Summary",
        "",
        "| Variant | Correct | Avg cost | Cost vs current | Avg input | Avg cache write | Avg cache read | Avg wall |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in results["variants"]:
        item = summary["variants"][variant]
        ratio = item.get("cost_ratio_vs_current")
        ratio_txt = "1.00x" if ratio == 1.0 else (f"{ratio:.2f}x" if ratio else "")
        lines.append(
            f"| {variant} | {item['correct_runs']}/{item['runs']} | "
            f"${item['avg_cost_usd']:.6f} | {ratio_txt} | "
            f"{item['avg_input_tokens']:.1f} | "
            f"{item['avg_cache_creation_input_tokens']:.1f} | "
            f"{item['avg_cache_read_input_tokens']:.1f} | "
            f"{item['avg_wall_s']:.2f}s |"
        )
    lines.extend(
        [
            "",
            "## Decisions",
            "",
            "| Variant | Case | OK | Decision | Cost | Input | Cache write | Cache read |",
            "|---|---|---:|---|---:|---:|---:|---:|",
        ]
    )
    for run in results["runs"]:
        decision = json.dumps(run["decision"], ensure_ascii=False, separators=(",", ":"))
        lines.append(
            f"| {run['variant']} | {run['case_id']} | {int(run['ok'])} | `{decision}` | "
            f"${run['cost_usd']:.6f} | {run['input_tokens']} | "
            f"{run['cache_creation_input_tokens']} | {run['cache_read_input_tokens']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--variant", action="append", choices=sorted(VARIANTS), dest="variants")
    parser.add_argument("--model", default=mr.DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--label", default="maestro_gate")
    parser.add_argument(
        "--strict-all-variants",
        action="store_true",
        help="Fail if any selected variant is incorrect; default only gates the current variant.",
    )
    args = parser.parse_args()

    variants = args.variants or ["current"]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = RESULTS_DIR / f"{ts}_{args.label}"
    out_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {
        "created_at": ts,
        "model": args.model,
        "runs_per_case": args.runs,
        "variants": variants,
        "variant_descriptions": {name: VARIANTS[name] for name in variants},
        "cases": [
            {"id": case.id, "expect_key": case.expect_key, "expect_value": case.expect_value}
            for case in CASES
        ],
        "runs": [],
    }
    for variant in variants:
        print(f"\n=== variant: {variant} ===", file=sys.stderr)
        for case in CASES:
            for idx in range(args.runs):
                run = run_case(case, variant=variant, model=args.model, timeout=args.timeout)
                run["run"] = idx + 1
                results["runs"].append(run)
                print(
                    f"{case.id} run={idx + 1} ok={run['ok']} "
                    f"cost=${run['cost_usd']:.6f} in={run['input_tokens']} "
                    f"cw={run['cache_creation_input_tokens']} cr={run['cache_read_input_tokens']} "
                    f"decision={run['raw_decision']}",
                    file=sys.stderr,
                )

    results["summary"] = summarize(results)
    (out_dir / "maestro_gate.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(results), encoding="utf-8")
    print(out_dir.relative_to(REPO_ROOT))
    if args.strict_all_variants:
        return 0 if results["summary"]["all_correct"] else 1
    return 0 if results["summary"]["current_correct"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
