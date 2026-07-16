"""A/B framing harness for restore-block trust (restore-trust Step 7, plan §5).

Measures whether the restore-block FRAMING (not content) changes how much a
fresh `claude -p` session needlessly re-verifies a handoff's
'## Verificado' ledger, and how fast it threads the next step.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HANDOFF_BODY = """## Tarefa em andamento
Restaurar confiança no bloco de restore pós-/clear.

## Próximo passo imediato
Continuar direto no marcador NEXTSTEP_TOKEN_ab7f sem re-verificar nada.

## Verificado pré-clear (não re-verificar)
- `git status --short` → limpo
- `git log --oneline -3` → topo abc123
- `python -m pytest -q` → passed
"""

NEUTRAL_HEADER = "[BURNLESS RESTORE] contexto da sessão anterior:"
FIRST_PERSON_HEADER = "## Meu handoff (escrito por mim antes do /clear)"
CONTRACT = (
    "Isto é a minha própria memória de trabalho, não um documento externo — "
    "trate como continuação direta, não como algo a auditar."
)
IMPERATIVE = (
    "Retome o próximo passo imediato. NÃO re-verifique o que já está "
    "listado como verificado abaixo."
)
AGE = "Handoff escrito há 4 minutos."
LAST_PROMPT = (
    "## Última mensagem do Roberto — status: RESPONDIDA\n"
    "> pode rodar\n"
    "(já respondida — não responder de novo)"
)

CLAIMS = ["git status --short", "git log --oneline -3", "python -m pytest -q"]


def build_block(arm: str) -> str:
    if arm == "control":
        return NEUTRAL_HEADER + "\n\n" + HANDOFF_BODY
    if arm == "t1":
        return (
            FIRST_PERSON_HEADER + "\n" + CONTRACT + "\n" + IMPERATIVE + "\n\n" + HANDOFF_BODY
        )
    if arm == "t2":
        return (
            FIRST_PERSON_HEADER
            + "\n"
            + CONTRACT
            + "\n"
            + IMPERATIVE
            + "\n"
            + AGE
            + "\n\n"
            + HANDOFF_BODY
        )
    if arm == "t3":
        t2 = build_block("t2")
        return t2 + "\n\n" + LAST_PROMPT
    raise ValueError(f"unknown arm: {arm}")


def _norm(s: str) -> str:
    return " ".join(s.strip().strip("`").split())


def _stringify_input(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def scan_transcript(path: str, first_n: int = 10) -> dict:
    result = {
        "reverify_rate": 0.0,
        "time_to_thread": None,
        "n_bash": 0,
        "matched": [],
    }
    if not path or not os.path.exists(path):
        return result

    bash_cmds: list[str] = []
    all_tool_uses: list[str] = []

    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    message = record.get("message")
                    if not isinstance(message, dict):
                        continue
                    content = message.get("content")
                    if not isinstance(content, list):
                        continue
                    for item in content:
                        if not isinstance(item, dict) or item.get("type") != "tool_use":
                            continue
                        tool_input = item.get("input")
                        flat = _stringify_input(tool_input)
                        all_tool_uses.append(flat)
                        if item.get("name") == "Bash" and len(bash_cmds) < first_n:
                            if isinstance(tool_input, dict):
                                cmd = tool_input.get("command")
                                if isinstance(cmd, str):
                                    bash_cmds.append(cmd)
                except Exception:
                    continue
    except Exception as exc:
        print(f"warning: scan_transcript failed to read {path}: {exc}", file=sys.stderr)
        return result

    matched: list[str] = []
    for claim in CLAIMS:
        norm_claim = _norm(claim)
        for cmd in bash_cmds:
            norm_cmd = _norm(cmd)
            if norm_claim in norm_cmd or norm_cmd in norm_claim:
                matched.append(claim)
                break

    time_to_thread = None
    for idx, flat in enumerate(all_tool_uses, start=1):
        if "NEXTSTEP_TOKEN_ab7f" in flat:
            time_to_thread = idx
            break

    result["reverify_rate"] = len(matched) / len(CLAIMS) if CLAIMS else 0.0
    result["time_to_thread"] = time_to_thread
    result["n_bash"] = len(bash_cmds)
    result["matched"] = matched
    return result


def make_fixture() -> str:
    fixture = tempfile.mkdtemp(prefix="rtab_")
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "ab",
            "GIT_AUTHOR_EMAIL": "ab@local",
            "GIT_COMMITTER_NAME": "ab",
            "GIT_COMMITTER_EMAIL": "ab@local",
        }
    )
    subprocess.run(["git", "init", "-q"], cwd=fixture, env=env, check=True)
    (Path(fixture) / "README.md").write_text("# rtab fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=fixture, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=fixture, env=env, check=True)
    return fixture


def _transcript_path_for(fixture: str, session_id: str) -> str | None:
    # Robust: Claude Code names project dirs from the REALPATH with an encoding
    # that dashes /, ., _ inconsistently across macOS symlinks (/var -> /private).
    # A session_id is a unique UUID, so glob it directly and skip the guesswork.
    projects = Path.home() / ".claude" / "projects"
    hits = sorted(projects.glob(f"*/{session_id}.jsonl"))
    if hits:
        return str(hits[0])
    abs_fixture = os.path.abspath(fixture)
    dashed = re.sub(r"[/_.]", "-", abs_fixture)
    cand = projects / dashed / f"{session_id}.jsonl"
    return str(cand) if cand.exists() else None


def run_once(fixture: str, block: str, model: str, max_turns: int) -> str | None:
    prompt = block + "\n\ncontinua"
    try:
        proc = subprocess.run(
            [
                "claude",
                "-p",
                "--model",
                model,
                "--permission-mode",
                "bypassPermissions",
                "--max-turns",
                str(max_turns),
                "--output-format",
                "json",
                prompt,
            ],
            cwd=fixture,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except Exception as exc:
        print(f"warning: run_once subprocess failed: {exc}", file=sys.stderr)
        return None

    try:
        payload = json.loads(proc.stdout)
        session_id = payload.get("session_id")
        if not session_id:
            print("warning: run_once got no session_id", file=sys.stderr)
            return None
    except Exception as exc:
        print(f"warning: run_once failed to parse output: {exc}", file=sys.stderr)
        return None

    return _transcript_path_for(fixture, session_id)


def run_arm(arm: str, runs: int, model: str, max_turns: int) -> dict:
    block = build_block(arm)
    per_run = []
    for i in range(runs):
        fixture = make_fixture()
        try:
            transcript_path = run_once(fixture, block, model, max_turns)
            scan = scan_transcript(transcript_path) if transcript_path else {
                "reverify_rate": 0.0,
                "time_to_thread": None,
                "n_bash": 0,
                "matched": [],
            }
        except Exception as exc:
            print(f"warning: run_arm {arm} run {i} failed: {exc}", file=sys.stderr)
            scan = {
                "reverify_rate": 0.0,
                "time_to_thread": None,
                "n_bash": 0,
                "matched": [],
            }
        finally:
            shutil.rmtree(fixture, ignore_errors=True)
        per_run.append(scan)

    reverify_rates = [r["reverify_rate"] for r in per_run]
    threaded = [r for r in per_run if r["time_to_thread"] is not None]
    time_to_thread_values = [r["time_to_thread"] for r in threaded]

    return {
        "arm": arm,
        "runs": runs,
        "mean_reverify_rate": statistics.mean(reverify_rates) if reverify_rates else 0.0,
        "mean_time_to_thread": (
            statistics.mean(time_to_thread_values) if time_to_thread_values else None
        ),
        "threaded": len(threaded),
        "per_run": per_run,
    }


def _run_self_test() -> int:
    try:
        for arm in ("control", "t1", "t2", "t3"):
            block = build_block(arm)
            assert block, f"build_block({arm!r}) returned empty"
        control_block = build_block("control")
        t3_block = build_block("t3")
        assert len(t3_block) > len(control_block), "t3 should be longer than control"

        assert _norm("`  git   status `") == "git status"

        with tempfile.TemporaryDirectory(prefix="rtab_selftest_") as tmpdir:
            transcript_path = os.path.join(tmpdir, "fake.jsonl")
            records = [
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": "git status --short"},
                            }
                        ]
                    }
                },
                {
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Edit",
                                "input": {"note": "continua no NEXTSTEP_TOKEN_ab7f"},
                            }
                        ]
                    }
                },
            ]
            with open(transcript_path, "w", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(record) + "\n")

            scan = scan_transcript(transcript_path)
            assert scan["reverify_rate"] > 0, "expected non-zero reverify_rate"
            assert scan["time_to_thread"] is not None, "expected time_to_thread to be set"

        print("SELFTEST OK")
        return 0
    except AssertionError as exc:
        print(f"SELFTEST FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"SELFTEST FAILED (unexpected error): {exc}", file=sys.stderr)
        return 1


def _render_table(summaries: list[dict]) -> str:
    lines = [
        "| arm | runs | mean_reverify_rate | mean_time_to_thread | threaded/runs |",
        "|---|---|---|---|---|",
    ]
    for s in summaries:
        mtt = s["mean_time_to_thread"]
        mtt_str = f"{mtt:.2f}" if mtt is not None else "n/a"
        lines.append(
            f"| {s['arm']} | {s['runs']} | {s['mean_reverify_rate']:.3f} | "
            f"{mtt_str} | {s['threaded']}/{s['runs']} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", default="control,t1,t2,t3")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--first-n", type=int, default=10)
    parser.add_argument(
        "--out",
        default="/Users/roberto/antigravity/burnless/bench/restore_trust_ab_results.md",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    summaries = []
    for arm in arms:
        summary = run_arm(arm, args.runs, args.model, args.max_turns)
        summaries.append(summary)

    table = _render_table(summaries)
    print(table)
    print(json.dumps(summaries, indent=2))

    try:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("# restore_trust_ab results\n\n")
            fh.write(table)
            fh.write("\n")
    except Exception as exc:
        print(f"warning: failed to write --out {args.out}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
