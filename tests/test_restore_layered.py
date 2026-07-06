"""P6/A1 (+I1): priority-layered restore assembly + always-present manifest.

The old behavior truncated the MIDDLE of the payload (head+tail), which in a
dense session destroyed exactly the thread: living_md Decisões/Refs and the
most recent pending exchanges. These tests pin the layered behavior:

1. metadata header always whole
2. 'Foco atual' + 'Threads abertas' never truncated
3. most recent pending exchanges whole, newest first, while they fit
4. rest of living_md
5. old pending exchanges as one-line summaries (seq N · first PERGUNTA line)
6. '## Manifesto' block ALWAYS present, never truncated (I1)
"""
from __future__ import annotations

import pytest

from burnless import recovery


def _dense_living_md(target_chars: int = 6000) -> str:
    decisions = []
    i = 0
    while True:
        i += 1
        decisions.append(
            f"- [state] decisão {i:03d}: consolidar o pipeline de rollover parte {i} "
            f"sem perder o fio da meada [seq {i}]"
        )
        md = _living_from(decisions)
        if len(md) >= target_chars:
            return md


def _living_from(decisions: list[str]) -> str:
    return "\n".join(
        [
            "## Foco atual",
            "- [doctrine] objetivo: retomada pós-/clear sem perder o fio FOCO-LITERAL",
            "- Próximo passo: validar o restore denso PROXIMO-PASSO-LITERAL",
            "",
            "## Threads abertas",
            "- [inflight] thread aberta: golden harness pendente THREAD-LITERAL [seq 2]",
            "",
            "## Decisões",
            *decisions,
            "",
            "## Contracts",
            "- /src/burnless/recovery.py — render_restore assinatura estável",
            "",
            "## Refs",
            "- /src/burnless/recovery.py#L1366-L1499 — restore denso [seq 3]",
            "",
            "## Riscos",
            "- [state] risco: truncamento cego do meio [seq 4]",
            "",
            "## Última validação",
            "- pytest tests/test_epoch_recovery_core.py OK",
            "",
            "## Recuperáveis",
            "- d101 — pytest tests/test_epochs_v3.py [seq 5]",
            "",
        ]
    )


def _seed_dense(root, n_pending=8, pending_chars=2000, living_chars=6000):
    living_md = _dense_living_md(living_chars)
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-dense",
        process_instance_id="proc-1",
        living_md=living_md,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    for i in range(1, n_pending + 1):
        filler = f"detalhe da troca {i} " * (pending_chars // 40)
        env = {
            "schema": 1,
            "host": "claude",
            "host_session_id": "sid-dense",
            "process_instance_id": "proc-1",
            "transcript_path": "/tmp/transcript-dense.jsonl",
            "exchange_id": f"sha256:dense-{i}",
            "user_text": f"PERGUNTA-{i}: como avançar a etapa {i} do plano denso?\n{filler}",
            "assistant_text": f"RESPOSTA-{i}: executando etapa {i}.\n{filler}",
            "files": [f"/tmp/app/src/step_{i}.py"],
        }
        recovery.journal_append(root, env)
    return living_md


def _restore(root, budget_tokens=2000, sid="sid-dense"):
    return recovery.render_restore(
        root,
        host="claude",
        host_session_id=sid,
        process_instance_id="proc-1",
        new_session_id="sid-new",
        source="clear",
        budget_tokens=budget_tokens,
    )


def test_dense_restore_keeps_the_thread(tmp_path):
    root = tmp_path / ".burnless"
    _seed_dense(root)

    payload = _restore(root, budget_tokens=2000)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    meta = payload["recovery"]

    # payload within budget (2000 tokens ≈ 8000 chars)
    assert len(ctx) <= 2000 * 4
    assert meta["truncated"] is True

    # layer 1: metadata header whole
    assert ctx.startswith("[BURNLESS RESTORE]")
    assert "pending_count=8" in ctx

    # layer 2: Foco atual + Threads abertas literal, never truncated
    assert "FOCO-LITERAL" in ctx
    assert "PROXIMO-PASSO-LITERAL" in ctx
    assert "THREAD-LITERAL" in ctx

    # layer 3: the most recent pending exchange is whole (both halves literal)
    assert "PERGUNTA-8: como avançar a etapa 8 do plano denso?" in ctx
    assert "RESPOSTA-8: executando etapa 8." in ctx
    assert "exchange_id: sha256:dense-8" in ctx
    assert meta["pending_whole"] >= 1

    # layer 5: exchanges that did not fit appear as one-line summaries
    assert meta["pending_whole"] + meta["pending_summarized"] <= 8
    assert meta["pending_summarized"] >= 1
    assert "- seq 1 · PERGUNTA-1: como avançar a etapa 1 do plano denso?" in ctx
    # ...and their bodies are NOT pasted
    assert "RESPOSTA-1: executando etapa 1." not in ctx

    # the old middle-truncation marker is dead
    assert "\n...\n[truncated]\n...\n" not in ctx


def test_dense_restore_has_untruncated_manifest(tmp_path):
    root = tmp_path / ".burnless"
    _seed_dense(root)
    # a previous session left an export artifact behind
    exports_dir = root / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    export_file = exports_dir / "epoch-claude-sid-dens-20260706T000000Z.md"
    export_file.write_text("---\nschema: burnless-epoch-export/v1\n---\n## Foco atual\n- x\n", encoding="utf-8")

    payload = _restore(root, budget_tokens=2000)
    ctx = payload["hookSpecificOutput"]["additionalContext"]

    assert "## Manifesto (leia sob demanda, não tudo)" in ctx
    manifest = ctx[ctx.index("## Manifesto") :]
    assert "- checkpoint completo: " in manifest
    assert "- journal: " in manifest
    assert "(head=8, applied=0)" in manifest
    assert f"- exports da sessão anterior: {export_file}" in manifest
    assert "leia só o que a tarefa atual pedir" in manifest
    # I1: pointer rule in the header
    assert "Refs e Recuperáveis são PONTEIROS" in ctx


def test_small_payload_served_whole_with_manifest(tmp_path):
    root = tmp_path / ".burnless"
    env = {
        "schema": 1,
        "host": "claude",
        "host_session_id": "sid-1",
        "process_instance_id": "proc-1",
        "transcript_path": "/tmp/t.jsonl",
        "exchange_id": "sha256:one",
        "user_text": "pergunta pendente",
        "assistant_text": "resposta pendente",
        "files": ["/tmp/app/src/main.py"],
    }
    recovery.journal_append(root, env)
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    payload = _restore(root, budget_tokens=2000, sid="sid-1")
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    meta = payload["recovery"]

    assert meta["truncated"] is False
    assert meta["pending_whole"] == 1
    assert meta["pending_summarized"] == 0
    # living_md verbatim, exchange whole
    assert "## Foco atual\n- objetivo vivo" in ctx
    assert "pergunta pendente" in ctx
    assert "resposta pendente" in ctx
    # manifest present even when nothing was truncated (I1 supersedes the old
    # truncation-only 'Referencia local' block)
    assert "## Manifesto (leia sob demanda, não tudo)" in ctx
    assert "## Referencia local" not in ctx


def test_non_v3_fallback_truncates_only_the_tail(tmp_path):
    root = tmp_path / ".burnless"
    lines = [f"linha {i:04d} do documento legado sem seções V3" for i in range(1, 401)]
    living_md = "INICIO-LITERAL\n" + "\n".join(lines)
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-legacy",
        process_instance_id="proc-1",
        living_md=living_md,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    env = {
        "schema": 1,
        "host": "claude",
        "host_session_id": "sid-legacy",
        "process_instance_id": "proc-1",
        "transcript_path": "/tmp/t.jsonl",
        "exchange_id": "sha256:legacy-1",
        "user_text": "PERGUNTA-LEGADO: continuar?",
        "assistant_text": "RESPOSTA-LEGADO: sim.",
        "files": [],
    }
    recovery.journal_append(root, env)

    payload = _restore(root, budget_tokens=1000, sid="sid-legacy")
    ctx = payload["hookSpecificOutput"]["additionalContext"]

    assert payload["recovery"]["truncated"] is True
    assert len(ctx) <= 1000 * 4
    # head preserved, middle intact up to the cut, only the tail dropped
    assert "INICIO-LITERAL" in ctx
    assert "linha 0001 do documento legado" in ctx
    assert "\n...\n[truncated]\n...\n" not in ctx
    assert "[living_md truncado — leia o checkpoint completo no Manifesto]" in ctx
    # manifest survives
    assert "## Manifesto (leia sob demanda, não tudo)" in ctx


def test_restore_owner_event_reports_layering(tmp_path):
    root = tmp_path / ".burnless"
    _seed_dense(root)
    _restore(root, budget_tokens=2000)

    import json

    log_path = root / ".burnless" / "owner_loop.jsonl"
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    served = [e for e in events if e.get("event") == "restore_served"]
    assert served, "restore_served event missing"
    last = served[-1]
    assert last["truncated"] is True
    assert last["pending_whole"] >= 1
    assert last["pending_summarized"] >= 1
