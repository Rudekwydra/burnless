from __future__ import annotations

import json
import subprocess
import time

from burnless import recovery


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _write_legacy_pool_handoff(root, sid, *, pid, claimed_by=None, cwd=None):
    handoff_dir = root / "epochs" / "_rolling" / "handoffs"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "host": "claude",
        "host_session_id": sid,
        "old_sid": sid,
        "process_instance_id": pid,
        "cwd": cwd,
        "claimed_by": claimed_by,
        "claimed_at": None,
        "created_at": "2026-06-01T00:00:00Z",
    }
    (handoff_dir / f"{sid}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_migration_creates_chain_for_unclaimed_legacy_handoff(tmp_path):
    root = tmp_path / ".burnless"
    _write_legacy_pool_handoff(root, "old-sid-1", pid="proc-legacy-1", cwd="/tmp/proj")

    result = recovery.migrate_legacy_handoff_pool(root, host="claude")

    assert result["migrated"] == ["old-sid-1.json"]
    chain_id = recovery._find_chain_id_by_pid(root, "claude", "proc-legacy-1")
    assert chain_id is not None
    chain_handoff = json.loads((root / "epochs" / "_rolling" / "chains" / chain_id / "handoff.json").read_text(encoding="utf-8"))
    assert chain_handoff["old_sid"] == "old-sid-1"


def test_migration_archives_already_claimed_legacy_handoff_without_chain(tmp_path):
    root = tmp_path / ".burnless"
    _write_legacy_pool_handoff(root, "old-sid-2", pid="proc-legacy-2", claimed_by="some-newer-sid")

    result = recovery.migrate_legacy_handoff_pool(root, host="claude")

    assert result["archived"] == ["old-sid-2.json"]
    assert recovery._find_chain_id_by_pid(root, "claude", "proc-legacy-2") is None
    assert (root / "epochs" / "_rolling" / "handoffs" / "_migrated" / "old-sid-2.json").exists()


def test_migration_is_idempotent(tmp_path):
    root = tmp_path / ".burnless"
    _write_legacy_pool_handoff(root, "old-sid-3", pid="proc-legacy-3")

    first = recovery.migrate_legacy_handoff_pool(root, host="claude")
    second = recovery.migrate_legacy_handoff_pool(root, host="claude")

    assert first["migrated"] == ["old-sid-3.json"]
    assert second["migrated"] == []
    assert second["skipped"] == ["old-sid-3.json"]


def test_gc_archives_dead_chain_older_than_7_days_and_exports(tmp_path):
    root = tmp_path / ".burnless"
    dead = _dead_pid()
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="gc-sid",
        process_instance_id=str(dead),
        living_md="## Foco atual\n- trabalho a ser exportado no GC\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    recovery.write_handoff(root, host="claude", host_session_id="gc-sid", process_instance_id=str(dead))
    chain_id = recovery._find_chain_id_by_pid(root, "claude", str(dead))
    assert chain_id is not None
    meta_path = root / "epochs" / "_rolling" / "chains" / chain_id / "chain.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    old_last_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 86400))
    meta["last_seen"] = old_last_seen
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    result = recovery.gc_dead_chains(root, host="claude")

    assert result["archived"] == [chain_id]
    assert not (root / "epochs" / "_rolling" / "chains" / chain_id).exists()
    assert (root / "epochs" / "_rolling" / "chains" / "_archived" / chain_id / "chain.json").exists()
    exports_dir = root / "exports"
    assert exports_dir.exists() and any(exports_dir.glob("*.md"))


def test_gc_ignores_live_chain_regardless_of_age(tmp_path):
    import os

    root = tmp_path / ".burnless"
    recovery.write_handoff(root, host="claude", host_session_id="live-sid", process_instance_id=str(os.getpid()))
    chain_id = recovery._find_chain_id_by_pid(root, "claude", str(os.getpid()))
    meta_path = root / "epochs" / "_rolling" / "chains" / chain_id / "chain.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30 * 86400))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    result = recovery.gc_dead_chains(root, host="claude")

    assert result["archived"] == []
    assert (root / "epochs" / "_rolling" / "chains" / chain_id).exists()
