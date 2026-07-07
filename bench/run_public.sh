#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export BURNLESS_PUBLIC_BENCH_NO_LLM=1
unset ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY GEMINI_API_KEY MISTRAL_API_KEY COHERE_API_KEY
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AZURE_OPENAI_API_KEY

PYTHON_BIN="${PYTHON:-python3}"

"$PYTHON_BIN" - "$ROOT" "$@" <<'PY'
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(sys.argv[1]).resolve()
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import yaml
except Exception as exc:  # pragma: no cover - public bench fails loudly.
    raise SystemExit(f"missing dependency PyYAML; run: pip install -e .\n{exc}")

try:
    import tiktoken
except Exception as exc:  # pragma: no cover - public bench fails loudly.
    raise SystemExit(f"missing dependency tiktoken; run: pip install -e .\n{exc}")

from burnless import __version__, exporting, recovery  # noqa: E402
import burnless.epochs_v2 as epochs_v2  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "golden" / "refactor_dense.yaml"
RESULTS_DIR = ROOT / "bench" / "results"
COMMAND = "bench/run_public.sh"
README_READY = "markdown block ready to paste into README"
ITERATIONS_DEFAULT = 100
STABILITY_TOLERANCE = 0.10
TOKEN_SWEEP_TURNS = (20, 50, 100)
TOKEN_ROLLOVER_CADENCE_TURNS = 8
LLM_MARKERS = (
    "anthropic",
    "openai",
    "gemini",
    "google-generativeai",
    "google-genai",
    "claude",
    "codex",
    "ollama",
    "litellm",
    "mistral",
    "cohere",
)
DIRECTIVE_RE = re.compile(
    r"\[\[(FOCO|NEXT|DECIDE|SUPERSEDE|THREAD\+|THREAD-|REF|RISK|VALID|CONTRACT):\s*(.+?)\]\]",
    re.DOTALL,
)
SEQ_BLOCK_RE = re.compile(r"^### seq (\d+)$", re.MULTILINE)
PAD_SENTENCE = (
    "Contexto adicional da troca: logs, diffs e saida de testes registrados "
    "no transcript real desta sessao de trabalho. "
)


class LLMCallDetected(RuntimeError):
    pass


class LLMGuard:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._orig_run = subprocess.run
        self._orig_popen = subprocess.Popen

    def _record_if_llm(self, cmd: Any) -> None:
        if isinstance(cmd, (list, tuple)):
            text = " ".join(str(part) for part in cmd)
            exe = Path(str(cmd[0])).name if cmd else ""
        else:
            text = str(cmd)
            exe = text.split()[0] if text.split() else ""
        lowered = text.lower()
        exe_lowered = exe.lower()
        if any(marker == exe_lowered or marker in lowered for marker in LLM_MARKERS):
            self.calls.append(text)
            raise LLMCallDetected(f"LLM command attempted in critical path: {text}")

    def patch(self) -> None:
        guard = self

        def guarded_run(cmd, *args, **kwargs):
            guard._record_if_llm(cmd)
            return guard._orig_run(cmd, *args, **kwargs)

        class GuardedPopen(subprocess.Popen):
            def __init__(self, cmd, *args, **kwargs):
                guard._record_if_llm(cmd)
                super().__init__(cmd, *args, **kwargs)

        subprocess.run = guarded_run  # type: ignore[assignment]
        subprocess.Popen = GuardedPopen  # type: ignore[assignment]

    def restore(self) -> None:
        subprocess.run = self._orig_run  # type: ignore[assignment]
        subprocess.Popen = self._orig_popen  # type: ignore[assignment]


@contextmanager
def no_llm_guard():
    guard = LLMGuard()
    env_backup = dict(os.environ)
    for key in list(os.environ):
        upper = key.upper()
        if upper.endswith("_API_KEY") or upper in {
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_ORG_ID",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
        }:
            os.environ.pop(key, None)
    os.environ["BURNLESS_PUBLIC_BENCH_NO_LLM"] = "1"
    guard.patch()
    try:
        yield guard
    finally:
        guard.restore()
        os.environ.clear()
        os.environ.update(env_backup)


def extract_fenced_block(prompt: str, header: str) -> str:
    idx = prompt.find(header)
    if idx == -1:
        return ""
    start = prompt.find("```", idx)
    if start == -1:
        return ""
    start = prompt.index("\n", start) + 1
    end = prompt.find("\n```", start)
    return prompt[start:end] if end != -1 else ""


def empty_doc() -> dict[str, list[str]]:
    return {section: [] for section in epochs_v2.SECTIONS_V3}


def fake_rewriter(prompt: str) -> str:
    previous_md = extract_fenced_block(prompt, "## Documento anterior")
    exchange = extract_fenced_block(prompt, "## Nova troca/evento")
    parsed = epochs_v2.parse_living_v3(previous_md) if previous_md.strip() else empty_doc()
    for section in epochs_v2.SECTIONS_V3:
        parsed.setdefault(section, [])

    matches = list(SEQ_BLOCK_RE.finditer(exchange))
    for idx, match in enumerate(matches):
        seq = int(match.group(1))
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(exchange)
        block = exchange[match.end() : end]
        for directive in DIRECTIVE_RE.finditer(block):
            kind, arg = directive.group(1), " ".join(directive.group(2).split())
            if kind == "FOCO":
                rest = [line for line in parsed["Foco atual"] if line.startswith("Próximo passo:")]
                parsed["Foco atual"] = [f"[doctrine] {arg} [seq {seq}]"] + rest
            elif kind == "NEXT":
                parsed["Foco atual"] = [
                    line for line in parsed["Foco atual"] if not line.startswith("Próximo passo:")
                ] + [f"Próximo passo: {arg} [seq {seq}]"]
            elif kind == "DECIDE":
                parsed["Decisões"].append(f"[state] {arg} [seq {seq}]")
            elif kind == "SUPERSEDE":
                old, _, new = arg.partition("=>")
                parsed["Decisões"] = [line for line in parsed["Decisões"] if old.strip() not in line]
                parsed["Decisões"].append(f"[state] {new.strip()} [seq {seq}]")
            elif kind == "THREAD+":
                parsed["Threads abertas"].append(f"[inflight] {arg} [seq {seq}]")
            elif kind == "THREAD-":
                parsed["Threads abertas"] = [line for line in parsed["Threads abertas"] if arg not in line]
            elif kind == "REF":
                parsed["Refs"].append(f"{arg} [seq {seq}]")
            elif kind == "RISK":
                parsed["Riscos"].append(f"[state] {arg} [seq {seq}]")
            elif kind == "VALID":
                parsed["Última validação"] = [f"{arg} [seq {seq}]"]
            elif kind == "CONTRACT":
                parsed["Contracts"].append(arg)
    return epochs_v2._rebuild_md_v3(parsed)


def failing_rewriter(_prompt: str):
    return None


def load_scenario() -> dict[str, Any]:
    if not FIXTURE.exists():
        raise SystemExit(f"missing fixture: {FIXTURE}")
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def flatten_fixture_turns(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for session_index, session in enumerate(scenario["sessions"]):
        for turn in session["turns"]:
            cloned = dict(turn)
            cloned["_fixture_session_index"] = session_index
            cloned["_fixture_rewriter"] = session.get("rewriter")
            turns.append(cloned)
    if not turns:
        raise RuntimeError("fixture has no turns")
    return turns


def synthesize_turn(base_turn: dict[str, Any], seq: int, fixture_turn_count: int) -> dict[str, Any]:
    cycle = (seq - 1) // fixture_turn_count
    if cycle == 0:
        return {key: value for key, value in base_turn.items() if not key.startswith("_")}
    turn = {key: value for key, value in base_turn.items() if not key.startswith("_")}
    replay_tag = f"\n\n[deterministic replay {cycle}, synthetic seq {seq}]"
    turn["user"] = f"{turn['user']}{replay_tag}"
    turn["assistant"] = f"{turn['assistant']}{replay_tag}"
    return turn


def synthesize_sweep_scenario(scenario: dict[str, Any], turns: int) -> dict[str, Any]:
    if turns <= 1:
        raise RuntimeError("token sweep depth must be greater than one turn")
    fixture_turns = flatten_fixture_turns(scenario)

    synthetic_turns = [
        synthesize_turn(fixture_turns[(seq - 1) % len(fixture_turns)], seq, len(fixture_turns))
        for seq in range(1, turns + 1)
    ]
    sessions = []
    for start in range(0, turns, TOKEN_ROLLOVER_CADENCE_TURNS):
        chunk = synthetic_turns[start : start + TOKEN_ROLLOVER_CADENCE_TURNS]
        session_index = len(sessions)
        session: dict[str, Any] = {"stop_compact": True, "turns": chunk}
        # Preserve the dense harness shape: every third session simulates a
        # deterministic rewriter outage, accumulating pending exchanges.
        if session_index % 3 == 2:
            session["rewriter"] = "fail"
        sessions.append(session)

    cloned = dict(scenario)
    cloned["name"] = f"{scenario.get('name', 'scenario')}_sweep_{turns}"
    cloned["description"] = (
        f"Deterministic replay/synthesis of the golden dense fixture to {turns} turns."
    )
    cloned["sessions"] = sessions
    return cloned


def pad_text(text: str, pad_to: int) -> str:
    while len(text) < pad_to:
        text += "\n" + PAD_SENTENCE
    return text


def prepare_project(project: Path, scenario: dict[str, Any]) -> Path:
    root = project / ".burnless"
    config = scenario.get("config")
    if config:
        root.mkdir(parents=True, exist_ok=True)
        (root / "config.yaml").write_text(config, encoding="utf-8")
    return root


def run_dense_pipeline(
    project: Path, scenario: dict[str, Any], expected_rollovers: int | None = 3
) -> tuple[list[dict[str, Any]], list[str]]:
    root = prepare_project(project, scenario)
    host = "claude"
    pid = "proc-public-bench"
    restores: list[dict[str, Any]] = []
    transcript_before_rollover: list[str] = []
    running_transcript: list[str] = []
    turn_no = 0
    sessions = scenario["sessions"]

    for session_index, session in enumerate(sessions):
        sid = f"sid-public-{session_index + 1}"
        rewriter = failing_rewriter if session.get("rewriter") == "fail" else fake_rewriter
        turns = session["turns"]
        for turn_index, turn in enumerate(turns):
            turn_no += 1
            user_text = pad_text(turn["user"], int(turn.get("pad_to") or 0))
            assistant_text = pad_text(turn["assistant"], int(turn.get("pad_to") or 0))
            running_transcript.append(
                f"### seq {turn_no}\nUser: {user_text}\nAssistant: {assistant_text}"
            )
            recovery.journal_append(
                root,
                {
                    "schema": 1,
                    "host": host,
                    "host_session_id": sid,
                    "process_instance_id": pid,
                    "transcript_path": f"/tmp/public-bench-{sid}.jsonl",
                    "exchange_id": f"sha256:public-bench-{turn_no:04d}",
                    "user_text": user_text,
                    "assistant_text": assistant_text,
                    "files": turn.get("files") or [],
                    "source": "stop",
                },
            )
            is_last_turn = turn_index == len(turns) - 1
            if session.get("stop_compact", True) and not is_last_turn:
                recovery.compact_pending(
                    root,
                    host=host,
                    host_session_id=sid,
                    process_instance_id=pid,
                    rewriter=rewriter,
                    budget_tokens=int(scenario.get("compact_budget_tokens") or 2500),
                    source="stop",
                )

        if session_index == len(sessions) - 1:
            break

        transcript_before_rollover.append("\n\n".join(running_transcript))
        new_sid = f"sid-public-{session_index + 2}"
        recovery.write_handoff(root, host=host, host_session_id=sid, process_instance_id=pid)
        claimed = recovery.claim_handoff(
            root, host=host, process_instance_id=pid, new_session_id=new_sid
        )
        if claimed is None:
            raise RuntimeError(f"handoff not claimed at rollover {session_index + 1}")
        old_sid = str(claimed.get("host_session_id"))
        payload = recovery.render_restore(
            root,
            host=host,
            host_session_id=old_sid,
            process_instance_id=pid,
            new_session_id=new_sid,
            source="clear",
            budget_tokens=scenario.get("budget_tokens"),
        )
        if payload is None:
            raise RuntimeError(f"empty restore at rollover {session_index + 1}")
        restores.append(payload)
        recovery.inherit_checkpoint(
            root,
            host=host,
            new_session_id=new_sid,
            process_instance_id=pid,
            old_session_id=old_sid,
        )
        recovery.compact_pending(
            root,
            host=host,
            host_session_id=old_sid,
            process_instance_id=pid,
            rewriter=rewriter,
            budget_tokens=int(scenario.get("compact_budget_tokens") or 2500),
            source="clear",
        )
        exporting.export_epoch(root, host=host, host_session_id=old_sid)

    if expected_rollovers is not None and len(restores) != expected_rollovers:
        raise RuntimeError(f"expected {expected_rollovers} rollovers, got {len(restores)}")
    return restores, transcript_before_rollover


def percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("empty sample")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * pct
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def measure_restore_latency(scenario: dict[str, Any], iterations: int, tmp_parent: Path) -> dict[str, Any]:
    samples: list[float] = []
    llm_calls: list[str] = []
    for i in range(iterations):
        project = tmp_parent / f"restore-{i:04d}"
        project.mkdir(parents=True, exist_ok=True)
        with no_llm_guard() as guard:
            started = time.perf_counter_ns()
            run_dense_pipeline(project, scenario)
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            if guard.calls:
                llm_calls.extend(guard.calls)
        shutil.rmtree(project, ignore_errors=True)
        samples.append(elapsed_ms)
    if llm_calls:
        raise LLMCallDetected("\n".join(llm_calls))
    return {
        "iterations": iterations,
        "p50_ms": percentile(samples, 0.50),
        "p95_ms": percentile(samples, 0.95),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "samples_ms": samples,
    }


def token_counter():
    encoding = tiktoken.get_encoding("cl100k_base")
    return lambda text: len(encoding.encode(text))


def measure_token_economy_depth(
    scenario: dict[str, Any], turns: int, tmp_parent: Path
) -> dict[str, Any]:
    project = tmp_parent / f"tokens-{turns}"
    project.mkdir(parents=True, exist_ok=True)
    sweep_scenario = synthesize_sweep_scenario(scenario, turns)
    expected_rollovers = len(sweep_scenario["sessions"]) - 1
    if expected_rollovers <= 0:
        raise RuntimeError(f"token sweep depth {turns} produced no rollovers")
    with no_llm_guard() as guard:
        restores, transcripts = run_dense_pipeline(
            project, sweep_scenario, expected_rollovers=expected_rollovers
        )
        if guard.calls:
            raise LLMCallDetected("\n".join(guard.calls))
    count_tokens = token_counter()
    without_rows = []
    with_rows = []
    for index, transcript in enumerate(transcripts, start=1):
        without_rows.append({"rollover": index, "tokens": count_tokens(transcript)})
    for index, payload in enumerate(restores, start=1):
        context = payload["hookSpecificOutput"]["additionalContext"]
        with_rows.append(
            {
                "rollover": index,
                "tokens": count_tokens(context),
                "chars": len(context),
                "pending_summarized": payload["recovery"]["pending_summarized"],
                "truncated": payload["recovery"]["truncated"],
            }
        )
    total_without = sum(row["tokens"] for row in without_rows)
    total_with = sum(row["tokens"] for row in with_rows)
    if total_without <= 0 or total_with <= 0:
        raise RuntimeError("token measurement produced non-positive totals")
    return {
        "turns": turns,
        "rollovers": len(restores),
        "without_burnless_tokens": total_without,
        "with_burnless_tokens": total_with,
        "saved_tokens": total_without - total_with,
        "saved_pct": (total_without - total_with) / total_without * 100,
        "without_burnless_by_rollover": without_rows,
        "with_burnless_by_rollover": with_rows,
    }


def classify_trend(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = []
    for prev, curr in zip(rows, rows[1:]):
        deltas.append(
            {
                "from_turns": prev["turns"],
                "to_turns": curr["turns"],
                "saved_pct_delta_points": curr["saved_pct"] - prev["saved_pct"],
            }
        )
    if all(delta["saved_pct_delta_points"] > 0 for delta in deltas):
        verdict = "crescente"
    elif all(abs(delta["saved_pct_delta_points"]) < 0.05 for delta in deltas):
        verdict = "plana"
    elif all(delta["saved_pct_delta_points"] < 0 for delta in deltas):
        verdict = "decrescente"
    else:
        verdict = "não monotônica"
    return {
        "verdict": verdict,
        "monotonically_increasing": verdict == "crescente",
        "deltas": deltas,
    }


def measure_token_economy(scenario: dict[str, Any], tmp_parent: Path) -> dict[str, Any]:
    sweep = [
        measure_token_economy_depth(scenario, turns, tmp_parent)
        for turns in TOKEN_SWEEP_TURNS
    ]
    trend = classify_trend(sweep)
    final = dict(sweep[-1])
    final["method"] = (
        "local cl100k_base tokens; identical estimator; token_economy_sweep depths "
        f"{'/'.join(str(turns) for turns in TOKEN_SWEEP_TURNS)}; deterministic replay/synthesis "
        f"of golden dense fixture; rollover cadence 1 per ~{TOKEN_ROLLOVER_CADENCE_TURNS} turns; "
        "no provider cache/cost claim"
    )
    final["token_economy_sweep"] = sweep
    final["trend"] = trend
    return final


def stable_enough(first: dict[str, Any], second: dict[str, Any]) -> tuple[bool, dict[str, float]]:
    deltas: dict[str, float] = {}
    ok = True
    for key in ("p50_ms", "p95_ms"):
        a = float(first[key])
        b = float(second[key])
        denominator = max(abs(a), abs(b), 0.001)
        delta = abs(a - b) / denominator
        deltas[key] = delta
        ok = ok and delta <= STABILITY_TOLERANCE
    return ok, deltas


def git_revision() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return "unknown"


def format_markdown(report: dict[str, Any]) -> str:
    latency = report["restore_latency"]
    tokens = report["token_economy"]
    stability = report["stability"]
    sweep_rows = "\n".join(
        "| {turns} | {without:,} | {with_:,} | {saved_pct:.1f}% |".format(
            turns=row["turns"],
            without=row["without_burnless_tokens"],
            with_=row["with_burnless_tokens"],
            saved_pct=row["saved_pct"],
        )
        for row in tokens["token_economy_sweep"]
    )
    trend = tokens["trend"]
    trend_deltas = ", ".join(
        "{from_turns}->{to_turns}: {saved_pct_delta_points:+.1f} pp".format(**delta)
        for delta in trend["deltas"]
    )
    return f"""## Burnless Public Benchmark

- {README_READY}
- Date: {report["date"]}
- Version: burnless {report["version"]} ({report["git"]})
- Reproduce: `{COMMAND}`
- Scenario: `tests/fixtures/golden/refactor_dense.yaml` deterministic sweep ({', '.join(str(turns) for turns in TOKEN_SWEEP_TURNS)} turns; rollover cadence 1 per ~{TOKEN_ROLLOVER_CADENCE_TURNS} turns)
- LLM calls in critical path: {report["llm_calls"]}

| Restore Metric | Value |
|---|---:|
| Restore p50 | {latency["p50_ms"]:.2f} ms |
| Restore p95 | {latency["p95_ms"]:.2f} ms |
| Restore iterations | {latency["iterations"]} |

| Turns | Without | With | Saved % |
|---:|---:|---:|---:|
{sweep_rows}

Trend verdict: {trend["verdict"]}; saved_pct deltas: {trend_deltas}.

Stability check: run1 p50={stability["run1"]["p50_ms"]:.2f} ms, p95={stability["run1"]["p95_ms"]:.2f} ms; run2 p50={stability["run2"]["p50_ms"]:.2f} ms, p95={stability["run2"]["p95_ms"]:.2f} ms; tolerance ±10%.

Token methodology: {tokens["method"]}. This reports replayed rollover payload tokens versus Burnless restore payload tokens, not a live LLM billing estimate.
"""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Public reproducible Burnless benchmark.")
    parser.add_argument("--iterations", type=int, default=ITERATIONS_DEFAULT)
    parser.add_argument("--keep-results", action="store_true")
    args = parser.parse_args(argv)
    if args.iterations <= 0:
        raise SystemExit("--iterations must be positive")

    scenario = load_scenario()
    total_turns = sum(len(session["turns"]) for session in scenario["sessions"])
    if total_turns < 20 or len(scenario["sessions"]) != 4:
        raise SystemExit("fixture must have 20+ turns and 4 sessions / 3 rollovers")

    tmp_parent = Path(tempfile.mkdtemp(prefix="burnless-public-bench-"))
    try:
        first = measure_restore_latency(scenario, args.iterations, tmp_parent)
        second = measure_restore_latency(scenario, args.iterations, tmp_parent)
        stable, deltas = stable_enough(first, second)
        tokens = measure_token_economy(scenario, tmp_parent)
    finally:
        if not args.keep_results:
            shutil.rmtree(tmp_parent, ignore_errors=True)

    report = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "version": __version__,
        "git": git_revision(),
        "command": COMMAND,
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "llm_calls": 0,
        "restore_latency": second,
        "token_economy": tokens,
        "token_economy_sweep": tokens["token_economy_sweep"],
        "stability": {"stable": stable, "deltas": deltas, "run1": first, "run2": second},
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"public_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(format_markdown(report))
    print(f"Evidence JSON: {out.relative_to(ROOT)}")

    if not stable:
        print(
            "ERROR: restore latency unstable beyond ±10%; reporting raw evidence instead of masking.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[2:]))
    except LLMCallDetected as exc:
        print(f"ERROR: LLM call detected; benchmark invariant violated.\n{exc}", file=sys.stderr)
        raise SystemExit(3)
PY
