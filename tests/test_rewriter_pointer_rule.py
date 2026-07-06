"""P6/I2: the rewriter prefers pointers over pasted content.

The V3 rewriter prompt must instruct that file-anchored facts become a Refs
line (`path#Lx-y — why [seq N]`) instead of pasted paragraphs in Decisões,
and every compaction logs its refs_ratio (Refs lines / total lines) via
owner_loop so the pointer-shape of the memory is observable.
"""
from __future__ import annotations

import json

import burnless.epochs_v2 as e
from burnless import recovery


def test_prompt_v3_carries_the_pointer_rule():
    prompt = e.living_rewrite_prompt_v3("", "troca citando /src/app/main.py#L10")

    assert "Regra de PONTEIRO" in prompt
    assert "a memória aponta, não cola" in prompt
    # file-anchored facts MUST become a Refs line...
    assert "DEVE virar UMA linha em 'Refs'" in prompt
    # ...never pasted paragraphs in Decisões
    assert "NUNCA um parágrafo em 'Decisões'" in prompt
    # content only when there is no source file
    assert "SOMENTE quando não existe arquivo-fonte" in prompt


def test_compact_prompt_inherits_pointer_rule():
    checkpoint = {"living_md": "", "applied_through": 0}
    pending = [
        {
            "seq": 1,
            "exchange_id": "sha256:x",
            "user_text": "ajusta o parser em /src/app/parser.py linhas 5-30",
            "assistant_text": "feito",
            "files": ["/src/app/parser.py"],
        }
    ]
    prompt = recovery._build_compact_prompt(checkpoint, pending)
    assert "Regra de PONTEIRO" in prompt


def test_compaction_logs_refs_ratio(tmp_path):
    root = tmp_path / ".burnless"
    recovery.journal_append(
        root,
        {
            "schema": 1,
            "host": "claude",
            "host_session_id": "sid-refs",
            "process_instance_id": "proc-1",
            "transcript_path": "/tmp/t.jsonl",
            "exchange_id": "sha256:refs-1",
            "user_text": "documenta o handler em /src/app/handler.py",
            "assistant_text": "handler documentado",
            "files": ["/src/app/handler.py"],
        },
    )

    candidate = "\n".join(
        [
            "## Foco atual",
            "- documentar handlers",
            "## Threads abertas",
            "- revisar rotas",
            "## Decisões",
            "- [state] handler documentado [seq 1]",
            "## Contracts",
            "## Refs",
            "- /src/app/handler.py#L1-40 — handler documentado [seq 1]",
            "- /src/app/routes.py — rotas relacionadas [seq 1]",
            "## Riscos",
            "## Última validação",
            "- revisão manual OK",
            "## Recuperáveis",
            "",
        ]
    )
    result = recovery.compact_pending(
        root,
        host="claude",
        host_session_id="sid-refs",
        process_instance_id="proc-1",
        rewriter=lambda _prompt: candidate,
        source="clear",
    )
    assert result["status"] == "committed"

    log_path = root / ".burnless" / "owner_loop.jsonl"
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    committed = [ev for ev in events if ev.get("event") == "checkpoint_committed"]
    assert committed
    ev = committed[-1]
    # 2 Refs lines out of 6 total entries
    assert ev["refs_lines"] == 2
    assert ev["total_lines"] == 6
    assert ev["refs_ratio"] == round(2 / 6, 3)
