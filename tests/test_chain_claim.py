from __future__ import annotations

import json
import os
import subprocess
import time

from burnless import recovery


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _write_chain_fixture(root, chain_id, *, host="claude", pid, cwd, last_seen=None, generation=1):
    chain_dir = root / "epochs" / "_rolling" / "chains" / chain_id
    chain_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "chain_id": chain_id,
        "host": host,
        "created": last_seen or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "last_seen": last_seen or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pid": pid,
        "pid_proc_name": "",
        "cwd": cwd,
        "generation": generation,
    }
    (chain_dir / "chain.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    handoff = {
        "schema": 1,
        "host": host,
        "host_session_id": f"sid-{chain_id}",
        "old_sid": f"sid-{chain_id}",
        "process_instance_id": pid,
        "chain_id": chain_id,
        "claimed_by": None,
        "claimed_at": None,
    }
    (chain_dir / "handoff.json").write_text(json.dumps(handoff, ensure_ascii=False), encoding="utf-8")
    return chain_dir


def test_claim_adopts_dead_pid_chain_same_cwd(tmp_path):
    root = tmp_path / ".burnless"
    dead = _dead_pid()
    _write_chain_fixture(root, "aaaa1111", pid=str(dead), cwd="/tmp/proj")

    claimed = recovery.claim_handoff(root, host="claude", process_instance_id="host-999888", new_session_id="new-sid", cwd="/tmp/proj")

    assert claimed is not None
    assert claimed["claim_mode"] == "adoption"
    assert claimed["old_sid"] == "sid-aaaa1111"
    assert claimed["claimed_by"] == "new-sid"


def test_claim_does_not_adopt_live_pid_chain(tmp_path):
    """Teste-B: janela A viva (pid = o proprio processo do teste) nunca eh roubada."""
    root = tmp_path / ".burnless"
    _write_chain_fixture(root, "bbbb2222", pid=str(os.getpid()), cwd="/tmp/proj")

    claimed = recovery.claim_handoff(root, host="claude", process_instance_id="host-999888", new_session_id="new-sid", cwd="/tmp/proj")

    assert claimed is None


def test_claim_ignores_cwd_mismatch_for_adoption(tmp_path):
    root = tmp_path / ".burnless"
    dead = _dead_pid()
    _write_chain_fixture(root, "cccc3333", pid=str(dead), cwd="/tmp/other-project")

    claimed = recovery.claim_handoff(root, host="claude", process_instance_id="host-999888", new_session_id="new-sid", cwd="/tmp/proj")

    assert claimed is None


def test_claim_ambiguous_adoption_when_two_dead_candidates(tmp_path):
    root = tmp_path / ".burnless"
    dead1 = _dead_pid()
    dead2 = _dead_pid()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Use timestamps within TTL window (86400s) — set one 30s ago, one 60s ago
    t1 = time.time() - 30
    t2 = time.time() - 60
    t1_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t1))
    t2_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t2))
    _write_chain_fixture(root, "dddd4444", pid=str(dead1), cwd="/tmp/proj", last_seen=t2_iso)
    _write_chain_fixture(root, "eeee5555", pid=str(dead2), cwd="/tmp/proj", last_seen=t1_iso)

    claimed = recovery.claim_handoff(root, host="claude", process_instance_id="host-999888", new_session_id="new-sid", cwd="/tmp/proj")

    assert claimed is not None
    assert claimed["claim_mode"] == "adoption_ambiguous"
    assert claimed["old_sid"] == "sid-eeee5555"


def test_claim_fresh_inherit_never_empty(tmp_path):
    """Teste-A: janela nova (sem chain nenhuma pra adotar) recebe o consolidado
    do projeto via inherit_checkpoint, nunca vazio."""
    root = tmp_path / ".burnless"
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="old-consolidated-sid",
        process_instance_id="old-consolidated-sid",
        living_md="## Foco atual\n- trabalho consolidado do projeto\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    claimed = recovery.claim_handoff(root, host="claude", process_instance_id="host-777666", new_session_id="brand-new-sid", cwd="/tmp/proj")

    assert claimed is None
    inherited = recovery.read_checkpoint(root, "claude", "brand-new-sid")
    assert inherited is not None
    assert "trabalho consolidado do projeto" in inherited["living_md"]
