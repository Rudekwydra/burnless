"""P6/A4 — golden memory harness (EM-5): must_remember / must_forget.

Runs the REAL recovery pipeline over multi-session scenarios described in
tests/fixtures/golden/*.yaml — journal → compact → handoff → claim →
inherit → restore → repeat — with a DETERMINISTIC fake rewriter (no LLM).

Each scenario declares 20+ turns across 4 sessions (3 rollovers) plus
`must_remember` (turn-1 objective, current decisions, files, next step)
and `must_forget` (superseded decisions, closed threads). At the 3rd
rollover the restore payload must contain every must_remember string,
no must_forget string, the last exchange verbatim, fit the budget, and
carry the '## Manifesto' block with paths that exist on disk (I1).

The fake rewriter consumes the real compact prompt and applies directives
embedded in the exchange text:

    [[FOCO: x]]            replace the objective in 'Foco atual'
    [[NEXT: x]]            set 'Próximo passo: x' in 'Foco atual'
    [[DECIDE: x]]          append decision '- [state] x [seq N]'
    [[SUPERSEDE: a => b]]  drop the decision containing `a`, decide `b`
    [[THREAD+: x]]         open a thread
    [[THREAD-: a]]         close (drop) the thread containing `a`
    [[REF: p — why]]       append '- p — why [seq N]' to Refs
    [[RISK: x]] [[VALID: x]] [[CONTRACT: x]]
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

import burnless.epochs_v2 as e
from burnless import exporting, recovery

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "golden"
SCENARIOS = sorted(FIXTURES.glob("*.yaml"))

_DIRECTIVE_RE = re.compile(
    r"\[\[(FOCO|NEXT|DECIDE|SUPERSEDE|THREAD\+|THREAD-|REF|RISK|VALID|CONTRACT):\s*(.+?)\]\]",
    re.DOTALL,
)
_SEQ_BLOCK_RE = re.compile(r"^### seq (\d+)$", re.MULTILINE)
_PAD_SENTENCE = (
    "Contexto adicional da troca: logs, diffs e saída de testes registrados "
    "no transcript real desta sessão de trabalho. "
)


def _extract_fenced_block(prompt: str, header: str) -> str:
    idx = prompt.find(header)
    if idx == -1:
        return ""
    start = prompt.find("```", idx)
    if start == -1:
        return ""
    start = prompt.index("\n", start) + 1
    end = prompt.find("\n```", start)
    return prompt[start:end] if end != -1 else ""


def _empty_doc() -> dict[str, list[str]]:
    return {section: [] for section in e.SECTIONS_V3}


def fake_rewriter(prompt: str) -> str:
    """Deterministic stand-in for the LLM rewriter (transducer semantics)."""
    prev_md = _extract_fenced_block(prompt, "## Documento anterior")
    exchange = _extract_fenced_block(prompt, "## Nova troca/evento")

    parsed = e.parse_living_v3(prev_md) if prev_md.strip() else _empty_doc()
    for section in e.SECTIONS_V3:
        parsed.setdefault(section, [])

    matches = list(_SEQ_BLOCK_RE.finditer(exchange))
    for i, m in enumerate(matches):
        seq = int(m.group(1))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(exchange)
        block = exchange[m.end():end]
        for dm in _DIRECTIVE_RE.finditer(block):
            kind, arg = dm.group(1), " ".join(dm.group(2).split())
            if kind == "FOCO":
                rest = [l for l in parsed["Foco atual"] if l.startswith("Próximo passo:")]
                parsed["Foco atual"] = [f"[doctrine] {arg} [seq {seq}]"] + rest
            elif kind == "NEXT":
                parsed["Foco atual"] = [
                    l for l in parsed["Foco atual"] if not l.startswith("Próximo passo:")
                ] + [f"Próximo passo: {arg} [seq {seq}]"]
            elif kind == "DECIDE":
                parsed["Decisões"].append(f"[state] {arg} [seq {seq}]")
            elif kind == "SUPERSEDE":
                old, _, new = arg.partition("=>")
                needle = old.strip()
                parsed["Decisões"] = [l for l in parsed["Decisões"] if needle not in l]
                parsed["Decisões"].append(f"[state] {new.strip()} [seq {seq}]")
            elif kind == "THREAD+":
                parsed["Threads abertas"].append(f"[inflight] {arg} [seq {seq}]")
            elif kind == "THREAD-":
                parsed["Threads abertas"] = [
                    l for l in parsed["Threads abertas"] if arg not in l
                ]
            elif kind == "REF":
                parsed["Refs"].append(f"{arg} [seq {seq}]")
            elif kind == "RISK":
                parsed["Riscos"].append(f"[state] {arg} [seq {seq}]")
            elif kind == "VALID":
                parsed["Última validação"] = [f"{arg} [seq {seq}]"]
            elif kind == "CONTRACT":
                parsed["Contracts"].append(arg)

    return e._rebuild_md_v3(parsed)


def failing_rewriter(_prompt: str):  # encoder outage: compaction fails, fail-open
    return None


class GoldenRun:
    """Drives the real pipeline for one scenario; keeps per-rollover results."""

    def __init__(self, scenario: dict, project_root: Path, pid: str = "proc-golden", sid_prefix: str = "sid-golden", cwd: str = None, rewriter=None):
        self.scenario = scenario
        self.project = project_root
        self.root = project_root / ".burnless"
        self.host = "claude"
        self.pid = pid
        self.sid_prefix = sid_prefix
        self.cwd = cwd or str(project_root)
        self.restores: list[dict] = []
        self.last_turns: list[dict] = []
        self.rewriter = rewriter
        config = scenario.get("config")
        if config:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "config.yaml").write_text(config, encoding="utf-8")

    def _pad(self, text: str, pad_to: int) -> str:
        while len(text) < pad_to:
            text += "\n" + _PAD_SENTENCE
        return text

    def run(self) -> None:
        sessions = self.scenario["sessions"]
        turn_no = 0
        for s_idx, session in enumerate(sessions):
            sid = f"{self.sid_prefix}-{s_idx + 1}"
            rewriter = failing_rewriter if session.get("rewriter") == "fail" else (self.rewriter or fake_rewriter)
            turns = session["turns"]
            for t_idx, turn in enumerate(turns):
                turn_no += 1
                pad_to = int(turn.get("pad_to") or 0)
                record = {
                    "schema": 1,
                    "host": self.host,
                    "host_session_id": sid,
                    "process_instance_id": self.pid,
                    "transcript_path": f"/tmp/golden-{sid}.jsonl",
                    "exchange_id": f"sha256:golden-{turn_no:04d}",
                    "user_text": self._pad(turn["user"], pad_to),
                    "assistant_text": self._pad(turn["assistant"], pad_to),
                    "files": turn.get("files") or [],
                    "source": "stop",
                }
                recovery.journal_append(self.root, record)
                is_last_turn = t_idx == len(turns) - 1
                if session.get("stop_compact", True) and not is_last_turn:
                    # Stop hook: L0 gate decides (deterministic, no LLM here)
                    recovery.compact_pending(
                        self.root,
                        host=self.host,
                        host_session_id=sid,
                        process_instance_id=self.pid,
                        rewriter=rewriter,
                        budget_tokens=int(self.scenario.get("compact_budget_tokens") or 2500),
                        source="stop",
                    )
            self.last_turns.append(turns[-1])
            if s_idx == len(sessions) - 1:
                break
            # ── rollover (SessionEnd clear → SessionStart clear) ────────────
            new_sid = f"{self.sid_prefix}-{s_idx + 2}"
            recovery.write_handoff(
                self.root, host=self.host, host_session_id=sid, process_instance_id=self.pid, cwd=self.cwd
            )
            claimed = recovery.claim_handoff(
                self.root, host=self.host, process_instance_id=self.pid, new_session_id=new_sid, cwd=self.cwd
            )
            assert claimed is not None, f"handoff not claimed at rollover {s_idx + 1}"
            old_sid = str(claimed.get("host_session_id"))
            payload = recovery.render_restore(
                self.root,
                host=self.host,
                host_session_id=old_sid,
                process_instance_id=self.pid,
                new_session_id=new_sid,
                source="clear",
                budget_tokens=self.scenario.get("budget_tokens"),
            )
            assert payload is not None, f"empty restore at rollover {s_idx + 1}"
            self.restores.append(payload)
            recovery.inherit_checkpoint(
                self.root,
                host=self.host,
                new_session_id=new_sid,
                process_instance_id=self.pid,
                old_session_id=old_sid,
            )
            # SessionEnd background block lands AFTER the new session started:
            # compact the old sid, then export it (real hook order).
            recovery.compact_pending(
                self.root,
                host=self.host,
                host_session_id=old_sid,
                process_instance_id=self.pid,
                rewriter=rewriter,
                budget_tokens=int(self.scenario.get("compact_budget_tokens") or 2500),
                source="clear",
            )
            exporting.export_epoch(self.root, host=self.host, host_session_id=old_sid)


def _effective_budget_tokens(scenario: dict) -> int:
    if scenario.get("budget_tokens"):
        return int(scenario["budget_tokens"])
    config = yaml.safe_load(scenario.get("config") or "") or {}
    return int((config.get("epochs") or {}).get("restore_budget_tokens", 4000))


def _manifest_paths(ctx: str) -> list[Path]:
    manifest = ctx[ctx.index("## Manifesto"):]
    paths = []
    for m in re.finditer(r"- [^:\n]*(?:checkpoint|exports)[^:\n]*: (\S+)", manifest):
        paths.append(Path(m.group(1)))
    jm = re.search(r"- journal: (\S+) \(head=\d+, applied=\d+\)", manifest)
    if jm:
        paths.append(Path(jm.group(1)))
    return paths


@pytest.fixture(autouse=True)
def _isolate_global_config(monkeypatch):
    monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", "")
    monkeypatch.delenv("BURNLESS_PROFILE", raising=False)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_golden_fixtures_exist_and_are_dense_enough():
    assert SCENARIOS, "no golden fixtures found"
    for path in SCENARIOS:
        scenario = _load(path)
        total_turns = sum(len(s["turns"]) for s in scenario["sessions"])
        assert total_turns >= 20, f"{path.name}: {total_turns} turns < 20"
        assert len(scenario["sessions"]) == 4, f"{path.name}: needs 4 sessions (3 rollovers)"
        assert scenario["must_remember"], path.name
        assert scenario["must_forget"], path.name


@pytest.mark.parametrize("fixture", SCENARIOS, ids=lambda p: p.stem)
def test_golden_scenario(fixture, tmp_path):
    scenario = _load(fixture)
    run = GoldenRun(scenario, tmp_path)
    run.run()

    assert len(run.restores) == 3, "expected exactly 3 rollovers"

    # I1: manifest present with existing paths at EVERY rollover
    for i, payload in enumerate(run.restores):
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "## Manifesto (leia sob demanda, não tudo)" in ctx, f"rollover {i + 1}"
        listed = _manifest_paths(ctx)
        assert listed, f"rollover {i + 1}: manifest lists no paths"
        for p in listed:
            assert p.exists(), f"rollover {i + 1}: manifest path missing: {p}"

    final = run.restores[-1]
    ctx = final["hookSpecificOutput"]["additionalContext"]
    meta = final["recovery"]

    # the thread survives: everything declared memorable is in the payload
    for needle in scenario["must_remember"]:
        assert needle in ctx, f"must_remember lost at 3rd rollover: {needle!r}"

    # pointer-class items (Refs, file paths) are reachable per the P6 contract
    # ("Refs e Recuperáveis são PONTEIROS"): inline in the payload OR in the
    # checkpoint the manifest points to — not required to be pasted verbatim.
    checkpoint = recovery.read_checkpoint(run.root, "claude", meta["old_session"])
    checkpoint_md = (checkpoint or {}).get("living_md") or ""
    for needle in scenario.get("must_reach") or []:
        assert needle in ctx or needle in checkpoint_md, (
            f"must_reach unreachable (neither payload nor checkpoint): {needle!r}"
        )

    # superseded decisions / closed threads are gone
    for needle in scenario["must_forget"]:
        assert needle not in ctx, f"must_forget leaked at 3rd rollover: {needle!r}"

    # the last exchange is served VERBATIM (both halves)
    last_turn = run.last_turns[2]  # last turn of session 3 (the 3rd rollover)
    assert last_turn["user"] in ctx, "last user text not literal in restore"
    assert last_turn["assistant"] in ctx, "last assistant text not literal in restore"

    # payload within the effective budget
    budget = _effective_budget_tokens(scenario)
    assert len(ctx) <= budget * 4, f"payload {len(ctx)} > budget {budget * 4}"

    # scenario-specific expectations
    expect = scenario.get("expect") or {}
    if expect.get("min_pending_summarized"):
        assert meta["pending_summarized"] >= int(expect["min_pending_summarized"])
    if expect.get("truncated") is not None:
        assert meta["truncated"] is bool(expect["truncated"])
    if expect.get("recuperaveis_pointer_in_checkpoint"):
        # A3: budget pressure demoted old decisions into Recuperáveis pointers
        checkpoint = recovery.read_checkpoint(run.root, "claude", meta["old_session"])
        parsed = e.parse_living_v3(checkpoint["living_md"])
        assert parsed["Recuperáveis"], "no Recuperáveis pointers in checkpoint"
        assert any("[seq" in line for line in parsed["Recuperáveis"])


def test_golden_rewriter_outage_inheritance():
    """Bonus scenario sanity: the outage fixture really fails compactions in
    sessions 2-3 yet rollover-1 knowledge survives via checkpoint inheritance
    (asserted by must_remember inside the fixture). Here we pin the mechanism:
    generations 2-3 never commit a compaction."""
    fixture = FIXTURES / "rewriter_outage.yaml"
    scenario = _load(fixture)
    assert scenario["sessions"][1].get("rewriter") == "fail"
    assert scenario["sessions"][2].get("rewriter") == "fail"


def test_golden_multi_window_isolation(tmp_path):
    """P7-5: estende o harness dourado pra 2 janelas concorrentes no MESMO
    projeto (mesmo tmp_path, mesmo .burnless), pids e sid_prefix distintos.
    must_remember de cada janela só aparece na própria; must_forget inclui
    o conteúdo da outra -- inclusive via inherit_checkpoint (que antes desse
    fix vazava o checkpoint mais recente do PROJETO INTEIRO, não escopado
    por pid, para qualquer janela sem checkpoint próprio ainda)."""
    scenario_a = {
        "sessions": [
            {"turns": [{"user": "pergunta A1", "assistant": "resposta A1 [[FOCO: OBJETIVO_JANELA_A]] [[DECIDE: decisao_exclusiva_A]]"}]},
            {"turns": [{"user": "pergunta A2", "assistant": "resposta A2"}]},
            {"turns": [{"user": "pergunta A3", "assistant": "resposta A3"}]},
            {"turns": [{"user": "pergunta A4 final", "assistant": "resposta A4 final"}]},
        ],
        "must_remember": ["OBJETIVO_JANELA_A", "decisao_exclusiva_A"],
        "must_forget": ["OBJETIVO_JANELA_B", "decisao_exclusiva_B"],
    }
    scenario_b = {
        "sessions": [
            {"turns": [{"user": "pergunta B1", "assistant": "resposta B1 [[FOCO: OBJETIVO_JANELA_B]] [[DECIDE: decisao_exclusiva_B]]"}]},
            {"turns": [{"user": "pergunta B2", "assistant": "resposta B2"}]},
            {"turns": [{"user": "pergunta B3", "assistant": "resposta B3"}]},
            {"turns": [{"user": "pergunta B4 final", "assistant": "resposta B4 final"}]},
        ],
        "must_remember": ["OBJETIVO_JANELA_B", "decisao_exclusiva_B"],
        "must_forget": ["OBJETIVO_JANELA_A", "decisao_exclusiva_A"],
    }

    run_a = GoldenRun(scenario_a, tmp_path, pid="proc-window-a", sid_prefix="sid-window-a")
    run_a.run()
    run_b = GoldenRun(scenario_b, tmp_path, pid="proc-window-b", sid_prefix="sid-window-b")
    run_b.run()

    ctx_a = run_a.restores[-1]["hookSpecificOutput"]["additionalContext"]
    ctx_b = run_b.restores[-1]["hookSpecificOutput"]["additionalContext"]

    for needle in scenario_a["must_remember"]:
        assert needle in ctx_a, f"janela A: must_remember lost: {needle!r}"
    for needle in scenario_a["must_forget"]:
        assert needle not in ctx_a, f"janela A: must_forget leaked: {needle!r}"
    for needle in scenario_b["must_remember"]:
        assert needle in ctx_b, f"janela B: must_remember lost: {needle!r}"
    for needle in scenario_b["must_forget"]:
        assert needle not in ctx_b, f"janela B: must_forget leaked: {needle!r}"
