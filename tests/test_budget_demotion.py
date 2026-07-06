"""P6/A3: enforce_budget_v3 demotes to Recuperáveis instead of deleting.

Excess Decisões/Refs lines migrate to '## Recuperáveis' as compact pointer
lines keeping their seq_origem marker. Real deletions happen only when
Recuperáveis itself is full (oldest first) and are reported via the
owner_loop event `budget_evicted` — nothing disappears silently.
"""
from __future__ import annotations

import json

import burnless.epochs_v2 as e


def _doc(n_decisoes=30, filler=400) -> str:
    foco = "## Foco atual\n- mantém foco\n\n"
    threads = "## Threads abertas\n- thread viva\n\n"
    decisoes = "## Decisões\n" + "".join(
        f"- decisão {i:02d} " + ("x" * filler) + f" [seq {i + 1}]\n" for i in range(n_decisoes)
    ) + "\n"
    rest = (
        "## Contracts\n\n## Refs\n"
        "- /src/app/main.py#L10-20 — ponto de entrada [seq 3]\n\n"
        "## Riscos\n\n## Última validação\n\n## Recuperáveis\n"
    )
    return foco + threads + decisoes + rest


def test_excess_decisoes_demote_to_recuperaveis_with_seq():
    md = _doc()
    assert len(md) // 4 > 2500

    out = e.enforce_budget_v3(md, budget_tokens=2500)
    assert len(out) // 4 <= 2500

    parsed = e.parse_living_v3(out)
    # oldest decisões left the section...
    joined = "\n".join(parsed["Decisões"])
    assert "decisão 00 " not in joined
    assert any("decisão 29 " in s for s in parsed["Decisões"])
    # ...but did NOT vanish: they are compact pointers in Recuperáveis,
    # seq_origem preserved
    recuperaveis = "\n".join(parsed["Recuperáveis"])
    assert "decisão 00" in recuperaveis
    assert "[seq 1]" in recuperaveis
    # pointers are compact, not the pasted 400-char body
    assert all(len(line) < 120 for line in parsed["Recuperáveis"])


def test_demoted_ref_becomes_path_pointer():
    foco = "## Foco atual\n- foco\n\n## Threads abertas\n\n"
    refs = "## Refs\n" + "".join(
        f"- /src/app/mod_{i}.py#L{i + 1}-{i + 9} — " + ("razão longa " * 60) + f"[seq {i + 1}]\n"
        for i in range(30)
    ) + "\n"
    tail = "## Decisões\n\n## Contracts\n\n## Riscos\n\n## Última validação\n\n## Recuperáveis\n"
    md = foco + refs + tail
    assert len(md) // 4 > 2500

    out = e.enforce_budget_v3(md, budget_tokens=2500, recoverables_max_items=50)
    parsed = e.parse_living_v3(out)
    recuperaveis = "\n".join(parsed["Recuperáveis"])
    # demoted refs keep path#L range + seq, drop the long why
    assert "/src/app/mod_0.py#L1-9 [seq 1]" in recuperaveis
    assert "razão longa" not in recuperaveis


def test_real_deletion_only_when_recuperaveis_full_and_event_logged(tmp_path):
    root = tmp_path
    md = _doc(n_decisoes=40)

    out = e.enforce_budget_v3(
        md,
        budget_tokens=2500,
        root=root,
        recoverables_max_items=5,
        event_context={"host_session_id": "sid-evict"},
    )
    parsed = e.parse_living_v3(out)
    # cap respected
    assert len(parsed["Recuperáveis"]) <= 5

    log_path = root / ".burnless" / "owner_loop.jsonl"
    assert log_path.exists()
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    evicts = [ev for ev in events if ev.get("event") == "budget_evicted"]
    assert evicts, "budget_evicted event missing"
    ev = evicts[-1]
    assert ev["host_session_id"] == "sid-evict"
    assert ev["evicted_count"] == len(ev["evicted_lines"]) > 0
    # the lost lines are listed (the oldest pointers)
    assert any("decisão 00" in line for line in ev["evicted_lines"])


def test_no_eviction_event_when_everything_fits_via_demotion(tmp_path):
    root = tmp_path
    md = _doc(n_decisoes=12)
    out = e.enforce_budget_v3(md, budget_tokens=2500, root=root, recoverables_max_items=50)
    assert len(out) // 4 <= 2500

    log_path = root / ".burnless" / "owner_loop.jsonl"
    if log_path.exists():
        events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert not [ev for ev in events if ev.get("event") == "budget_evicted"]


def test_under_budget_doc_untouched():
    md = _doc(n_decisoes=2, filler=10)
    assert e.enforce_budget_v3(md, budget_tokens=2500) == md
