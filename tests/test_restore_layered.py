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

import json

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
    assert "checkpoint" in manifest
    assert "- journal: " in manifest
    assert "(head=8, applied=0)" in manifest
    assert "exports" in manifest and str(export_file) in manifest
    assert "leia só o que a tarefa atual pedir" in manifest or "read only what the current task needs" in manifest
    # I1: pointer rule in the header
    assert "PONTEIROS" in ctx or "POINTERS" in ctx


def test_small_payload_served_whole_with_manifest(tmp_path):
    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    # Explicit en_markers: false to ensure PT markers
    config_file = root / "config.yaml"
    config_file.write_text("format:\n  en_markers: false\n", encoding="utf-8")
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

    log_path = root / "owner_loop.jsonl"
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    served = [e for e in events if e.get("event") == "restore_served"]
    assert served, "restore_served event missing"
    last = served[-1]
    assert last["truncated"] is True
    assert last["pending_whole"] >= 1
    assert last["pending_summarized"] >= 1


def test_last_prompt_section_journal_path(tmp_path):
    # Root 1: newest exchange answered -> RESPONDIDA + do-not-answer-again line.
    root = tmp_path / "answered" / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text("language: pt-BR\n", encoding="utf-8")
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    recovery.journal_append(
        root,
        {
            "schema": 1,
            "host": "claude",
            "host_session_id": "sid-1",
            "process_instance_id": "proc-1",
            "transcript_path": "/tmp/t.jsonl",
            "exchange_id": "sha256:one",
            "user_text": "PERGUNTA-1: primeira pergunta",
            "assistant_text": "RESPOSTA-1: primeira resposta",
            "files": [],
        },
    )
    recovery.journal_append(
        root,
        {
            "schema": 1,
            "host": "claude",
            "host_session_id": "sid-1",
            "process_instance_id": "proc-1",
            "transcript_path": "/tmp/t.jsonl",
            "exchange_id": "sha256:two",
            "user_text": "PERGUNTA-2: a mais recente pergunta do Roberto",
            "assistant_text": "RESPOSTA-2: resumo da resposta mais recente\ndetalhe adicional",
            "files": [],
        },
    )

    payload = _restore(root, budget_tokens=4000, sid="sid-1")
    ctx = payload["hookSpecificOutput"]["additionalContext"]

    assert "## Última mensagem do Roberto — status: RESPONDIDA" in ctx
    assert "> PERGUNTA-2: a mais recente pergunta do Roberto" in ctx
    assert "NÃO responder de novo" in ctx
    manifest_idx = ctx.index("## Manifesto (leia sob demanda, não tudo)")
    section_idx = ctx.index("## Última mensagem do Roberto — status: RESPONDIDA")
    assert section_idx > manifest_idx

    # Root 2: newest exchange NOT answered -> EM ABERTO, no do-not-answer line.
    root2 = tmp_path / "open" / ".burnless"
    root2.mkdir(parents=True, exist_ok=True)
    (root2 / "config.yaml").write_text("language: pt-BR\n", encoding="utf-8")
    recovery.write_checkpoint(
        root2,
        host="claude",
        host_session_id="sid-2",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    recovery.journal_append(
        root2,
        {
            "schema": 1,
            "host": "claude",
            "host_session_id": "sid-2",
            "process_instance_id": "proc-1",
            "transcript_path": "/tmp/t.jsonl",
            "exchange_id": "sha256:open-1",
            "user_text": "PERGUNTA-ABERTA: pergunta ainda sem resposta",
            "assistant_text": "",
            "files": [],
        },
    )

    payload2 = _restore(root2, budget_tokens=4000, sid="sid-2")
    ctx2 = payload2["hookSpecificOutput"]["additionalContext"]
    assert "status: EM ABERTO" in ctx2
    assert "NÃO responder de novo" not in ctx2

    # Root 3: newest user_text over 600 chars -> truncated with an ellipsis.
    root3 = tmp_path / "long" / ".burnless"
    root3.mkdir(parents=True, exist_ok=True)
    (root3 / "config.yaml").write_text("language: pt-BR\n", encoding="utf-8")
    recovery.write_checkpoint(
        root3,
        host="claude",
        host_session_id="sid-3",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    long_user_text = "PERGUNTA-LONGA: " + ("detalhe repetido " * 60)
    assert len(long_user_text) > 600
    recovery.journal_append(
        root3,
        {
            "schema": 1,
            "host": "claude",
            "host_session_id": "sid-3",
            "process_instance_id": "proc-1",
            "transcript_path": "/tmp/t.jsonl",
            "exchange_id": "sha256:long-1",
            "user_text": long_user_text,
            "assistant_text": "RESPOSTA-LONGA: ok",
            "files": [],
        },
    )

    payload3 = _restore(root3, budget_tokens=4000, sid="sid-3")
    ctx3 = payload3["hookSpecificOutput"]["additionalContext"]
    section_start = ctx3.index("## Última mensagem do Roberto")
    assert "…" in ctx3[section_start:]


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def test_last_user_prompt_from_transcript_picks_plain_user(tmp_path):
    transcript = tmp_path / "old-sid.jsonl"
    _write_jsonl(
        transcript,
        [
            {"message": {"role": "user", "content": "primeira pergunta"}},
            {"message": {"role": "assistant", "content": "primeira resposta"}},
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": "some tool output"}],
                }
            },
            {"message": {"role": "user", "content": "última pergunta sem resposta"}},
        ],
    )

    rec = recovery._last_user_prompt_from_transcript(transcript)
    assert rec is not None
    assert rec["user_text"] == "última pergunta sem resposta"
    assert rec["assistant_text"] == ""

    transcript2 = tmp_path / "old-sid-answered.jsonl"
    _write_jsonl(
        transcript2,
        [
            {"message": {"role": "user", "content": "primeira pergunta"}},
            {"message": {"role": "assistant", "content": "primeira resposta"}},
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": "some tool output"}],
                }
            },
            {"message": {"role": "user", "content": "última pergunta com resposta"}},
            {"message": {"role": "assistant", "content": "resposta final"}},
        ],
    )

    rec2 = recovery._last_user_prompt_from_transcript(transcript2)
    assert rec2 is not None
    assert rec2["user_text"] == "última pergunta com resposta"
    assert rec2["assistant_text"] == "resposta final"


def test_last_user_prompt_from_transcript_degrades(tmp_path):
    missing = tmp_path / "does-not-exist.jsonl"
    assert recovery._last_user_prompt_from_transcript(missing) is None

    malformed = tmp_path / "malformed.jsonl"
    malformed.parent.mkdir(parents=True, exist_ok=True)
    with malformed.open("w", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write(json.dumps({"message": {"role": "user", "content": "pergunta válida"}}) + "\n")
        f.write("{broken json\n")

    result = recovery._last_user_prompt_from_transcript(malformed)
    assert result is None or result["user_text"] == "pergunta válida"


def test_restore_transcript_fallback_when_no_pending(tmp_path):
    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text("language: pt-BR\n", encoding="utf-8")
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-old",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    new_transcript_path = transcripts_dir / "sid-new.jsonl"
    old_transcript_path = transcripts_dir / "sid-old.jsonl"
    _write_jsonl(
        old_transcript_path,
        [
            {"message": {"role": "user", "content": "pergunta do transcript de fallback"}},
        ],
    )

    payload = recovery.render_restore(
        root,
        host="claude",
        host_session_id="sid-old",
        process_instance_id="proc-1",
        new_session_id="sid-new",
        source="clear",
        transcript_path=str(new_transcript_path),
        budget_tokens=4000,
    )
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "## Última mensagem do Roberto" in ctx
    assert "pergunta do transcript de fallback" in ctx
