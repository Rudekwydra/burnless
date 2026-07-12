from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


def test_handoff_included_and_consumed(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    host = "claude"
    session_id = "sid-1"

    recovery.write_checkpoint(
        root,
        host=host,
        host_session_id=session_id,
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    handoff_path = root / "epochs" / "_rolling" / "live_handoff.md"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_text = "EU estava fazendo X"
    handoff_path.write_text(handoff_text, encoding="utf-8")

    payload = recovery.render_restore(
        root,
        host=host,
        host_session_id=session_id,
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source="clear",
    )

    ctx = payload["hookSpecificOutput"]["additionalContext"]
    meta = payload["recovery"]

    assert handoff_text in ctx, f"Handoff text not found in context: {ctx}"
    assert "## Handoff" in ctx
    assert not handoff_path.exists(), "Handoff file should be consumed (unlinked)"
    assert meta["live_handoff_chars"] == len(handoff_text)


def test_handoff_stale_ignored(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    host = "claude"
    session_id = "sid-1"

    recovery.write_checkpoint(
        root,
        host=host,
        host_session_id=session_id,
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    handoff_path = root / "epochs" / "_rolling" / "live_handoff.md"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text("EU estava fazendo X", encoding="utf-8")

    old_time = time.time() - 999999
    os.utime(handoff_path, (old_time, old_time))

    payload = recovery.render_restore(
        root,
        host=host,
        host_session_id=session_id,
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source="clear",
    )

    ctx = payload["hookSpecificOutput"]["additionalContext"]
    meta = payload["recovery"]

    assert "EU estava fazendo X" not in ctx, "Stale handoff should not appear"
    assert "## Handoff" not in ctx
    assert not handoff_path.exists(), "Stale handoff file should be deleted"
    assert meta["live_handoff_chars"] == 0


def test_handoff_absent_noop(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    host = "claude"
    session_id = "sid-1"

    recovery.write_checkpoint(
        root,
        host=host,
        host_session_id=session_id,
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    payload = recovery.render_restore(
        root,
        host=host,
        host_session_id=session_id,
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source="clear",
    )

    ctx = payload["hookSpecificOutput"]["additionalContext"]
    meta = payload["recovery"]

    assert "## Handoff" not in ctx
    assert "objetivo vivo" in ctx
    assert meta["live_handoff_chars"] == 0


def test_handoff_survives_budget(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    host = "claude"
    session_id = "sid-1"

    big_living_md = "## Decisões\n" + ("deciso importante linha " * 1000)

    recovery.write_checkpoint(
        root,
        host=host,
        host_session_id=session_id,
        process_instance_id="proc-1",
        living_md=big_living_md,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    handoff_path = root / "epochs" / "_rolling" / "live_handoff.md"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_text = "EU estava fazendo X em contexto crítico"
    handoff_path.write_text(handoff_text, encoding="utf-8")

    payload = recovery.render_restore(
        root,
        host=host,
        host_session_id=session_id,
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source="clear",
        budget_tokens=200,
    )

    ctx = payload["hookSpecificOutput"]["additionalContext"]
    meta = payload["recovery"]

    assert handoff_text in ctx, "Handoff should survive budget truncation"
    assert "## Handoff" in ctx
    assert meta["live_handoff_chars"] == len(handoff_text)
    assert meta["truncated"] is True
    assert not handoff_path.exists(), "Handoff file should be consumed"
