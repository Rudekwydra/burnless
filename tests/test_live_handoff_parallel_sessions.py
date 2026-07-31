from __future__ import annotations

import os
import time


def _seed_checkpoint(recovery, root, host, session_id):
    recovery.write_checkpoint(
        root,
        host=host,
        host_session_id=session_id,
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )


def test_path_is_session_scoped_and_legacy_without_session(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    legacy = recovery.live_handoff_path_for(root)
    scoped = recovery.live_handoff_path_for(root, "sid-A")
    other = recovery.live_handoff_path_for(root, "sid-B")

    assert legacy.name == "live_handoff.md"
    assert legacy.parent.name == "_rolling"
    assert scoped != legacy
    assert scoped != other
    assert scoped.parent.name == "handoffs"
    assert scoped.parent.is_dir()


def test_parallel_sessions_both_survive_the_restore(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    host = "claude"
    _seed_checkpoint(recovery, root, host, "sid-1")

    older = recovery.live_handoff_path_for(root, "sid-A")
    newer = recovery.live_handoff_path_for(root, "sid-B")
    older.write_text("FRENTE A viva", encoding="utf-8")
    newer.write_text("FRENTE B viva", encoding="utf-8")
    now = time.time()
    os.utime(older, (now - 600, now - 600))
    os.utime(newer, (now - 60, now - 60))

    payload = recovery.render_restore(
        root,
        host=host,
        host_session_id="sid-1",
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source="clear",
    )

    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "FRENTE B viva" in ctx
    assert "FRENTE A viva" in ctx
    assert not older.exists()
    assert not newer.exists()


def test_merge_is_newest_first_and_capped(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    now = time.time()
    for index in range(5):
        path = recovery.live_handoff_path_for(root, f"sid-{index}")
        path.write_text(f"BLOCO {index}", encoding="utf-8")
        os.utime(path, (now - index * 60, now - index * 60))

    result = recovery._consume_live_handoff(root)
    assert result is not None
    text, _age = result

    assert text.index("BLOCO 0") < text.index("BLOCO 1") < text.index("BLOCO 2")
    assert "BLOCO 3" not in text
    assert "BLOCO 4" not in text
    assert not recovery._session_live_handoffs(root)


def test_single_handoff_is_returned_verbatim(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    path = recovery.live_handoff_path_for(root, "sid-only")
    path.write_text("SO EU", encoding="utf-8")

    result = recovery._consume_live_handoff(root)
    assert result is not None
    assert result[0] == "SO EU"
