"""Restore footer must be budgeted within the context payload.

The footer is calculated early and reserved space-wise before truncating context.
This ensures payload (context + separator + footer) always fits the budget_tokens limit.
"""
from __future__ import annotations

import pytest

from burnless import epochs, recovery


@pytest.fixture(autouse=True)
def _isolate_global_config(monkeypatch):
    # config.load must not pick up the developer's ~/.config/burnless/config.yaml
    monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", "")
    monkeypatch.delenv("BURNLESS_PROFILE", raising=False)


def _seed(root, n_pending=6, pending_chars=3000):
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo denso\n\n## Threads abertas\n- thread viva\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    for i in range(1, n_pending + 1):
        filler = f"conteudo {i} " * (pending_chars // 12)
        recovery.journal_append(
            root,
            {
                "schema": 1,
                "host": "claude",
                "host_session_id": "sid-1",
                "process_instance_id": "proc-1",
                "transcript_path": "/tmp/t.jsonl",
                "exchange_id": f"sha256:budget-{i}",
                "user_text": f"pergunta {i}\n{filler}",
                "assistant_text": f"resposta {i}\n{filler}",
                "files": [],
            },
        )


def _restore(root, source="clear", budget_tokens=None):
    return recovery.render_restore(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source=source,
        budget_tokens=budget_tokens,
    )


def _ctx(payload):
    return payload["hookSpecificOutput"]["additionalContext"]


def test_footer_fits_within_budget_dense_scenario(tmp_path):
    """Payload total (context + footer) must fit within declared budget."""
    root = tmp_path / ".burnless"
    _seed(root, n_pending=10, pending_chars=800)

    budget_tokens = 2000
    payload = _restore(root, source="clear", budget_tokens=budget_tokens)
    ctx = _ctx(payload)

    max_chars = max(800, budget_tokens * 4)
    assert len(ctx) <= max_chars, (
        f"payload {len(ctx)} chars exceeds budget {max_chars} "
        f"({budget_tokens} tokens)"
    )


def test_footer_present_in_payload(tmp_path):
    """Footer must be present when it is not empty."""
    root = tmp_path / ".burnless"
    _seed(root)

    payload = _restore(root, source="clear")
    ctx = _ctx(payload)

    footer_text = epochs.build_restore_clear_footer()
    if footer_text:
        assert footer_text in ctx, "footer text not found in payload"


def test_empty_footer_no_separator(tmp_path):
    """When footer is empty, no separator should be added."""
    root = tmp_path / ".burnless"
    _seed(root)

    # Mock build_restore_clear_footer to return empty string
    import burnless.epochs as ep_module

    original_footer = ep_module.build_restore_clear_footer
    ep_module.build_restore_clear_footer = lambda: ""

    try:
        payload = _restore(root, source="clear")
        ctx = _ctx(payload)
        # Should not have trailing "\n\n" (from empty footer case)
        assert not ctx.endswith("\n\n"), "unexpected separator with empty footer"
    finally:
        ep_module.build_restore_clear_footer = original_footer


def test_footer_overhead_reserved_before_truncation(tmp_path):
    """Footer overhead must be deducted from budget before context truncation.

    This ensures the final payload (context + footer) respects the boundary.
    If the footer is ~130 chars and budget is 8000, the context should be
    truncated to ~7870 to leave room for footer + separator.
    """
    root = tmp_path / ".burnless"
    # Dense scenario: large pending queue
    _seed(root, n_pending=15, pending_chars=600)

    budget_tokens = 2000
    payload = _restore(root, source="clear", budget_tokens=budget_tokens)
    ctx = _ctx(payload)
    footer_text = epochs.build_restore_clear_footer()

    # Verify the math: context + separator + footer <= max_chars
    max_chars = max(800, budget_tokens * 4)
    expected_footer_overhead = (len(footer_text) + 2) if footer_text else 0

    # Find where footer starts in the payload
    if footer_text and footer_text in ctx:
        footer_idx = ctx.rfind(footer_text)
        actual_context_part = ctx[:footer_idx].rstrip()
        # Context part (without the footer) should fit in adjusted budget
        assert len(actual_context_part) + expected_footer_overhead + len(footer_text) <= max_chars
