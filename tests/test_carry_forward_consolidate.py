"""d739 — carry_forward_chain must consolidate predecessor living-docs into ONE
slotted doc (per-slot merge), not a newest-first stack of N whole docs.

Encodes the requirement:
- single-doc structure (exactly one `## Foco atual`)
- no orphaned chain (unique content of an older chain survives)
- newest-chain entry precedes older-chain entry inside a shared slot
"""
import os

from burnless import epochs, epochs_v2


def _write_living(root, chat_id, body):
    lp = epochs_v2.living_path(root, chat_id)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(body, encoding="utf-8")
    return lp


def test_carry_forward_consolidates_into_single_slotted_doc(tmp_path, monkeypatch):
    monkeypatch.setenv("BURNLESS_EPOCH_V2", "1")
    (tmp_path / ".burnless" / "epochs").mkdir(parents=True)

    # Chain B: older. Distinct Foco + a unique Decisão.
    lp_b = _write_living(
        tmp_path,
        "chatBBBB",
        "## Foco atual\n- tarefa antiga\n\n## Decisões\n- decisao-B\n\n",
    )
    # Chain A: fresher. Distinct Foco.
    lp_a = _write_living(
        tmp_path,
        "chatAAAA",
        "## Foco atual\n- owner-loop\n\n## Decisões\n- decisao-A\n\n",
    )

    # Force A strictly newer than B (mtime drives newest-first ranking).
    os.utime(lp_b, (1000, 1000))
    os.utime(lp_a, (2000, 2000))

    out = epochs.carry_forward_chain(tmp_path, current_chat_id="currentXX")

    # SINGLE-DOC STRUCTURE: exactly one `## Foco atual` (not one per chain).
    assert out.count("## Foco atual") == 1, out
    assert out.count("## Decisões") == 1, out

    # NO-ORPHAN: the unique content of the older chain B survives the merge.
    assert "decisao-B" in out, out
    assert "owner-loop" in out, out
    assert "tarefa antiga" in out, out

    # NEWEST-FIRST INSIDE SLOT: freshest chain's Foco precedes the older one.
    assert out.index("owner-loop") < out.index("tarefa antiga"), out


def test_carry_forward_v1_fallback_unchanged(tmp_path, monkeypatch):
    """No V2 living docs -> V1 NNN.md chain path still serves predecessor."""
    monkeypatch.setenv("BURNLESS_EPOCH_V2", "1")
    epochs_dir = tmp_path / ".burnless" / "epochs"
    epochs_dir.mkdir(parents=True)

    pred = epochs_dir / "chatV1AA"
    pred.mkdir()
    (pred / "000.md").write_text("## v1 checkpoint\n- conteudo v1\n", encoding="utf-8")

    out = epochs.carry_forward_chain(tmp_path, current_chat_id="currentXX")
    assert "conteudo v1" in out, out


def test_recon_semantic_preserves_consolidation(tmp_path, monkeypatch):
    """d740 defeito 1: semantic recon must fold the CONSOLIDATED doc, not the
    freshest raw chain — older chain's unique content must survive."""
    monkeypatch.setenv("BURNLESS_EPOCH_V2", "1")
    (tmp_path / ".burnless" / "epochs").mkdir(parents=True)

    lp_b = _write_living(
        tmp_path,
        "chatBBBB",
        "## Foco atual\n- tarefa antiga\n\n## Decisões\n- decisao-B-unica\n\n",
    )
    lp_a = _write_living(
        tmp_path,
        "chatAAAA",
        "## Foco atual\n- owner-loop\n\n## Decisões\n- decisao-A\n\n",
    )
    os.utime(lp_b, (1000, 1000))
    os.utime(lp_a, (2000, 2000))

    # Force the semantic-recon path: non-empty recon + config mode=semantic.
    monkeypatch.setattr(epochs, "_commits_since_mtime", lambda *a, **k: "- abc123 fez X\n")
    from burnless import config as _cfg
    monkeypatch.setattr(_cfg, "load", lambda *a, **k: {"epoch": {"resume_recon": "semantic"}})
    # Identity fold: whatever base_body is passed comes back verbatim. With the
    # bug, base_body = freshest raw chain only (decisao-B-unica lost). With the
    # fix, base_body = consolidated doc (decisao-B-unica survives).
    monkeypatch.setattr(epochs, "_semantic_recon", lambda root, base_body, recon: base_body)

    out = epochs.carry_forward_chain(tmp_path, current_chat_id="currentXX")

    assert "# living:reconciled" in out, out
    assert "decisao-B-unica" in out, out
    assert "owner-loop" in out, out


def test_cap_order_preserves_contracts_and_foco(tmp_path, monkeypatch):
    """d740 defeito 2: when over cap, trim Decisões/Refs/Riscos first; never
    trim Foco atual or Contracts."""
    monkeypatch.setenv("BURNLESS_EPOCH_V2", "1")
    (tmp_path / ".burnless" / "epochs").mkdir(parents=True)

    decisoes = "\n".join(f"- decisao-{i:04d}-{'x' * 50}" for i in range(400))
    body = (
        "## Foco atual\n- foco-vivo-critico\n\n"
        "## Contracts\n- contract-XYZ /Users/roberto/path/d999\n\n"
        f"## Decisões\n{decisoes}\n\n"
    )
    _write_living(tmp_path, "chatCAPX", body)

    out = epochs.carry_forward_chain(tmp_path, current_chat_id="currentXX")

    # Survivors: Foco atual + Contracts.
    assert "foco-vivo-critico" in out, out
    assert "contract-XYZ /Users/roberto/path/d999" in out, out
    # Decisões was trimmed (oldest = tail entries gone) to meet the cap.
    assert "decisao-0399" not in out, out
    assert len(out) <= 8000 + len("> ordem: documento vivo (living-doc v2) consolidado por slot — entradas mais NOVAS primeiro em cada secao\n\n") + 200, len(out)


def test_sources_footer_lists_predecessors(tmp_path, monkeypatch):
    """d740 defeito 3: a compact Sources footer keeps a pointer to the raw
    predecessor chat-ids (newest-first)."""
    monkeypatch.setenv("BURNLESS_EPOCH_V2", "1")
    (tmp_path / ".burnless" / "epochs").mkdir(parents=True)

    lp_b = _write_living(tmp_path, "chatBBBB", "## Foco atual\n- t-b\n\n")
    lp_a = _write_living(tmp_path, "chatAAAA", "## Foco atual\n- t-a\n\n")
    os.utime(lp_b, (1000, 1000))
    os.utime(lp_a, (2000, 2000))

    out = epochs.carry_forward_chain(tmp_path, current_chat_id="currentXX")

    assert "## Sources" in out, out
    assert "chatAAAA" in out, out
    assert "chatBBBB" in out, out
    # newest-first ordering inside the footer.
    assert out.index("chatAAAA") < out.index("chatBBBB"), out
