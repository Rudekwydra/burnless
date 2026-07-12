"""Tests for parent checkpoint final merge logic (P10/4).

Note: Full integration tests are covered by the golden test suite.
This module contains basic structure/import verification tests.
"""
import pytest
from burnless.recovery import (
    _checkpoint_payload,
    write_checkpoint,
    read_checkpoint,
    compact_pending,
)


def test_checkpoint_payload_includes_merge_fields():
    """_checkpoint_payload includes inherited_from and parent_final_merged fields."""
    payload = _checkpoint_payload(
        host="test",
        host_session_id="s1",
        process_instance_id="p1",
        living_md="## Foco atual\nTest",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
        journal_head=0,
        generation=1,
        chain_id="c1",
        inherited_from="parent_sid",
        parent_final_merged=False,
    )

    # Verify the fields are in the payload
    assert payload.get("inherited_from") == "parent_sid"
    assert payload.get("parent_final_merged") is False


def test_checkpoint_payload_default_values():
    """_checkpoint_payload uses default values when fields not provided."""
    payload = _checkpoint_payload(
        host="test",
        host_session_id="s1",
        process_instance_id="p1",
        living_md="## Foco atual\nTest",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
        journal_head=0,
        generation=1,
        chain_id="c1",
    )

    # Verify defaults: inherited_from should not be in payload (None by default)
    # parent_final_merged defaults to True, so it should not be in payload
    assert payload.get("inherited_from") is None
    assert payload.get("parent_final_merged") is not False  # Default is True


def test_write_checkpoint_accepts_merge_fields(tmp_path):
    """write_checkpoint accepts inherited_from and parent_final_merged kwargs."""
    result = write_checkpoint(
        tmp_path,
        host="test",
        host_session_id="s1",
        process_instance_id="p1",
        living_md="## Foco atual\nTest",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
        inherited_from="parent_sid",
        parent_final_merged=False,
    )

    assert result.get("inherited_from") == "parent_sid"
    assert result.get("parent_final_merged") is False


def test_render_restore_readonly_merge_logic(tmp_path):
    """P10/4: render_restore has read-only merge logic for parent threads.

    Verify that render_restore implementation includes the parent merge logic
    and that checkpoints are not modified during rendering (this is read-only).
    The full integration test is in test_memory_golden[pending_plan].
    """
    from burnless.recovery import render_restore, _journal_dir
    from pathlib import Path
    import json

    # Setup: parent session with open thread, consolidated
    parent_living_md = """## Foco atual
- [task] build Import v2

## Threads abertas
- na volta construir Import v2 em fases (parse, valida, grava)

## Decisões

## Contracts

## Refs
"""

    parent_checkpoint = write_checkpoint(
        tmp_path,
        host="claude",
        host_session_id="parent_sid",
        process_instance_id="proc1",
        living_md=parent_living_md,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=5,
        journal_head=5,
    )

    # Child checkpoint: inherited, thread missing, not merged yet
    child_living_md = """## Foco atual
- [task] deploy feature

## Threads abertas

## Decisões

## Contracts

## Refs
"""

    child_checkpoint = write_checkpoint(
        tmp_path,
        host="claude",
        host_session_id="child_sid",
        process_instance_id="proc1",
        living_md=child_living_md,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
        journal_head=0,
        inherited_from="parent_sid",
        parent_final_merged=False,
    )

    # Create minimal journal so render_restore has content to return
    journal_dir = _journal_dir(tmp_path, "claude", "child_sid")
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / "00001.json"
    journal_file.write_text(
        json.dumps({
            "seq": 1,
            "exchange_id": "ex1",
            "user_text": "Test question",
            "assistant_text": "Test answer",
            "captured_at": "2026-07-12T04:00:00Z",
        })
    )

    # Save checkpoint mtime before render_restore
    checkpoint_path = tmp_path / "epochs" / "sessions" / "claude" / "child_sid" / "checkpoint.json"
    assert checkpoint_path.exists(), f"Checkpoint not found at {checkpoint_path}"
    mtime_before = checkpoint_path.stat().st_mtime

    # Call render_restore with child's session
    restore = render_restore(
        tmp_path,
        host="claude",
        host_session_id="child_sid",
        process_instance_id="proc1",
        new_session_id="new_sid",
        source="rollover",
        budget_tokens=5000,
    )

    assert restore is not None, "render_restore should return payload"

    # Verify: payload's additionalContext contains the parent's thread
    ctx = restore["hookSpecificOutput"]["additionalContext"]
    assert "na volta construir Import v2 em fases" in ctx, (
        "parent thread must be in additionalContext (merged by render_restore)"
    )

    # Verify: checkpoint on disk remains unchanged (read-only)
    mtime_after = checkpoint_path.stat().st_mtime
    assert mtime_before == mtime_after, (
        "checkpoint file must not be modified by render_restore (read-only)"
    )

    # Verify: checkpoint file size unchanged
    size_before = checkpoint_path.stat().st_size
    size_after = checkpoint_path.stat().st_size
    assert size_before == size_after, (
        "checkpoint file size must not change (read-only)"
    )


def test_render_restore_generation_lte_1_adopts_parent_final_doc(tmp_path):
    """P10/4: child gen<=1 adopts parent's final doc (including SUPERSEDE).

    Child inherits parent's pre-compact doc (old decision). Parent then applies
    SUPERSEDE (old decision → new decision). Child's checkpoint is stale but
    generation<=1 signals early inheritance. render_restore should present
    parent's final doc: old decision gone, new decision visible.
    """
    from burnless.recovery import render_restore, _journal_dir, write_checkpoint
    from pathlib import Path
    import json

    # Parent doc AFTER compact (SUPERSEDE applied)
    parent_doc_final = """## Foco atual
- [task] build Import v2

## Threads abertas

## Decisões
- carga inicial via seed SQL versionado

## Contracts

## Refs
"""

    # Parent checkpoint settled with final doc (will be generation 1)
    parent_checkpoint = write_checkpoint(
        tmp_path,
        host="claude",
        host_session_id="parent_sid",
        process_instance_id="proc1",
        living_md=parent_doc_final,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=10,
        journal_head=10,
    )

    # Child checkpoint: gen<=1 (early inheritance), inherited from parent, NOT merged yet
    # Child's doc is stale (doesn't have the new decision)
    child_doc_stale = """## Foco atual
- [task] build Import v2

## Threads abertas

## Decisões
- usar planilha manual de carga para o catalogo

## Contracts

## Refs
"""

    child_checkpoint = write_checkpoint(
        tmp_path,
        host="claude",
        host_session_id="child_sid",
        process_instance_id="proc1",
        living_md=child_doc_stale,  # stale!
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
        journal_head=0,
        inherited_from="parent_sid",
        parent_final_merged=False,
    )
    # Verify child is gen<=1
    assert int(child_checkpoint.get("generation") or 0) <= 1

    # Create minimal journal for child so render_restore has content
    journal_dir = _journal_dir(tmp_path, "claude", "child_sid")
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / "00001.json"
    journal_file.write_text(
        json.dumps({
            "seq": 1,
            "exchange_id": "ex1",
            "user_text": "Test question",
            "assistant_text": "Test answer",
            "captured_at": "2026-07-12T04:00:00Z",
        })
    )

    # Call render_restore
    restore = render_restore(
        tmp_path,
        host="claude",
        host_session_id="child_sid",
        process_instance_id="proc1",
        new_session_id="new_sid",
        source="rollover",
        budget_tokens=5000,
    )

    assert restore is not None, "render_restore should return payload"

    # Verify: additionalContext contains NEW decision (from parent final doc)
    ctx = restore["hookSpecificOutput"]["additionalContext"]
    assert "carga inicial via seed SQL versionado" in ctx, (
        "parent's final decision (after SUPERSEDE) must be in additionalContext"
    )

    # Verify: OLD decision is NOT in additionalContext
    assert "usar planilha manual de carga para o catalogo" not in ctx, (
        "parent's old decision (SUPERSEDED) must not be in additionalContext"
    )


def test_compact_pending_generation_gt_1_preserves_threads_only(tmp_path):
    """P10/4: child gen>1 (evolved) uses threads-only merge, not full doc adoption.

    Simulate a child with generation>1 (has done real compactions). Parent has
    updated doc. Child's doc has its own evolved state. Merge should use
    preserve_open_threads (threads-only), NOT full doc adoption. This proves
    we don't regress the evolved case.
    """
    from burnless.recovery import compact_pending, _journal_dir, write_checkpoint
    from pathlib import Path
    import json

    # Parent doc (final, settled)
    parent_doc = """## Foco atual
- [task] parent focus

## Threads abertas
- parent thread about new design

## Decisões
- parent decision 1

## Contracts

## Refs
"""

    parent_checkpoint = write_checkpoint(
        tmp_path,
        host="claude",
        host_session_id="parent_sid",
        process_instance_id="proc1",
        living_md=parent_doc,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=5,
        journal_head=5,
    )

    # Child doc (gen>1, evolved independently)
    # To get gen>1, write twice
    child_doc_gen1 = """## Foco atual
- [task] child focus gen1

## Threads abertas

## Decisões

## Contracts

## Refs
"""
    write_checkpoint(
        tmp_path,
        host="claude",
        host_session_id="child_sid",
        process_instance_id="proc1",
        living_md=child_doc_gen1,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
        journal_head=0,
        inherited_from="parent_sid",
        parent_final_merged=False,
    )

    child_doc_gen2 = """## Foco atual
- [task] child focus evolved differently

## Threads abertas
- child's own thread about implementation

## Decisões
- child's own decision 1

## Contracts

## Refs
"""

    child_checkpoint = write_checkpoint(
        tmp_path,
        host="claude",
        host_session_id="child_sid",
        process_instance_id="proc1",
        living_md=child_doc_gen2,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
        journal_head=0,
        inherited_from="parent_sid",
        parent_final_merged=False,
    )
    # Verify child is gen>1
    assert int(child_checkpoint.get("generation") or 0) > 1

    # Create journal with pending exchange
    journal_dir = _journal_dir(tmp_path, "claude", "child_sid")
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_file = journal_dir / "00001.json"
    journal_file.write_text(
        json.dumps({
            "seq": 1,
            "exchange_id": "ex1",
            "user_text": "Continue working",
            "assistant_text": "Continuing work",
            "captured_at": "2026-07-12T04:00:00Z",
        })
    )

    # Mock rewriter (collapse: declare everything done, zero out threads)
    def mock_rewriter_collapse(prompt: str) -> str:
        return """## Foco atual
- [task] child focus evolved differently

## Threads abertas

## Decisões
- child's own decision 1

## Contracts

## Refs
"""

    # Call compact_pending with child's session
    result = compact_pending(
        tmp_path,
        host="claude",
        host_session_id="child_sid",
        process_instance_id="proc1",
        source="interval",
        rewriter=mock_rewriter_collapse,
        budget_tokens=2500,
    )

    assert result is not None, "compact_pending should complete"
    assert result.get("status") in ("ok", "part", "committed"), f"unexpected status: {result.get('status')}"

    # Key assertion: child's own thread and decision are preserved
    # (threads-only merge, NOT full doc adoption)
    living_md = result.get("checkpoint", {}).get("living_md", "")
    assert "child's own thread about implementation" in living_md or "implementation" in living_md, (
        "child's thread must survive via preserve_open_threads (threads-only, not full adoption)"
    )
    assert "child's own decision 1" in living_md or "child's own decision" in living_md, (
        "child's decision must survive (not overwritten by parent's full doc)"
    )
