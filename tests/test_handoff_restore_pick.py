"""Regression: post-/clear restore must pick the freshest VALID live handoff
deterministically, preferring a fresh non-empty child-project handoff over the
resolved root's own stale/empty one (divergent write-root incident 2026-07-16).
"""
from __future__ import annotations

import os
import time
from pathlib import Path


def _write_checkpoint(recovery, root, session_id="sid-1", living_md="## Foco atual\n- objetivo do root\n"):
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id=session_id,
        process_instance_id="proc-1",
        living_md=living_md,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )


def _own_handoff(root: Path, text: str, age_s: int = 0) -> Path:
    path = root / "epochs" / "_rolling" / "live_handoff.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return path


def _child_handoff(project: Path, name: str, text: str, age_s: int = 0) -> Path:
    path = project / name / ".burnless" / "epochs" / "_rolling" / "live_handoff.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return path


def _restore(recovery, root):
    return recovery.render_restore(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source="clear",
    )


def test_fresh_child_handoff_beats_stale_empty_own(tmp_path):
    """The 2026-07-16 incident: session launched at the parent root (own handoff
    empty/stale), work + handoff written in a child project. Restore must serve
    the child's content."""
    from burnless import recovery

    root = tmp_path / ".burnless"
    _write_checkpoint(recovery, root)
    own = _own_handoff(root, "", age_s=3600)
    child_text = "EU estava consertando o restore no subprojeto burnless"
    child = _child_handoff(tmp_path, "burnless", child_text, age_s=60)

    payload = _restore(recovery, root)
    ctx = payload["hookSpecificOutput"]["additionalContext"]

    assert child_text in ctx, f"child handoff not served: {ctx}"
    assert "## Handoff" in ctx
    assert not child.exists(), "chosen child handoff should be consumed"
    assert not own.exists(), "superseded own handoff should be consumed"
    assert payload["recovery"]["live_handoff_chars"] == len(child_text)


def test_empty_own_never_wins_even_when_newer(tmp_path):
    """Content-validity ranks above mtime: an own handoff that is empty but
    NEWER than the child's must never be preferred."""
    from burnless import recovery

    root = tmp_path / ".burnless"
    _write_checkpoint(recovery, root)
    child_text = "handoff valido do filho, mais velho que o vazio do root"
    _child_handoff(tmp_path, "subproj", child_text, age_s=1800)
    _own_handoff(root, "   \n\n  ", age_s=0)  # freshest, but whitespace-only

    payload = _restore(recovery, root)
    ctx = payload["hookSpecificOutput"]["additionalContext"]

    assert child_text in ctx
    assert payload["recovery"]["live_handoff_chars"] == len(child_text)


def test_valid_own_beats_older_child(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    _write_checkpoint(recovery, root)
    own_text = "handoff do proprio root, fresco e valido"
    _own_handoff(root, own_text, age_s=60)
    child_text = "handoff mais velho do filho"
    child = _child_handoff(tmp_path, "subproj", child_text, age_s=3600)

    payload = _restore(recovery, root)
    ctx = payload["hookSpecificOutput"]["additionalContext"]

    assert own_text in ctx
    assert child_text not in ctx
    assert child.exists(), "unchosen child handoff must be left for its own root's restore"


def test_stale_child_and_empty_own_yield_no_handoff(tmp_path):
    """TTL still applies to children; an empty own + expired child = no handoff
    section, but the checkpoint restore itself still renders."""
    from burnless import recovery

    root = tmp_path / ".burnless"
    _write_checkpoint(recovery, root)
    _own_handoff(root, "")
    child = _child_handoff(tmp_path, "subproj", "conteudo velho demais", age_s=999999)

    payload = _restore(recovery, root)
    ctx = payload["hookSpecificOutput"]["additionalContext"]

    assert "## Handoff" not in ctx
    assert "conteudo velho demais" not in ctx
    assert "objetivo do root" in ctx
    assert payload["recovery"]["live_handoff_chars"] == 0
    assert child.exists(), "expired child handoff is not this root's to delete"


def test_fresh_child_handoff_restores_even_without_own_checkpoint(tmp_path):
    """Divergent-root writes can land in a subproject before the parent root has
    any checkpoint: a fresh valid handoff alone must still produce a restore."""
    from burnless import recovery

    root = tmp_path / ".burnless"
    (root / "epochs" / "_rolling").mkdir(parents=True, exist_ok=True)
    child_text = "so o filho tem estado; restore nao pode voltar vazio"
    _child_handoff(tmp_path, "burnless", child_text, age_s=120)

    payload = _restore(recovery, root)

    assert payload is not None, "restore must not bail when a fresh valid handoff exists"
    assert child_text in payload["hookSpecificOutput"]["additionalContext"]


def test_consume_prefers_freshest_among_valid_children(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    (root / "epochs" / "_rolling").mkdir(parents=True, exist_ok=True)
    _child_handoff(tmp_path, "proj-a", "filho A mais velho", age_s=3000)
    _child_handoff(tmp_path, "proj-b", "filho B mais fresco", age_s=30)

    result = recovery._consume_live_handoff(root)

    assert result is not None
    text, age_s = result
    assert text == "filho B mais fresco"
    assert age_s < 3000


def test_hidden_dirs_are_not_child_projects(tmp_path):
    """`.burnless` itself (and any dot-dir) must never be scanned as a child
    project — guards against the nested-phantom `.burnless/.burnless` residue."""
    from burnless import recovery

    root = tmp_path / ".burnless"
    (root / "epochs" / "_rolling").mkdir(parents=True, exist_ok=True)
    phantom = root / ".burnless" / "epochs" / "_rolling" / "live_handoff.md"
    phantom.parent.mkdir(parents=True, exist_ok=True)
    phantom.write_text("lixo do phantom", encoding="utf-8")

    assert recovery._consume_live_handoff(root) is None
    assert phantom.exists()
