from __future__ import annotations

import json

from burnless import recovery


def test_write_handoff_creates_chain_dir_and_carries_chain_id(tmp_path):
    root = tmp_path / ".burnless"

    payload = recovery.write_handoff(
        root, host="claude", host_session_id="sid-1", process_instance_id="proc-1", cwd="/tmp/proj"
    )

    assert "chain_id" in payload
    chain_id = payload["chain_id"]
    chain_dir = root / "epochs" / "_rolling" / "chains" / chain_id
    assert (chain_dir / "chain.json").exists()
    assert (chain_dir / "handoff.json").exists()

    meta = json.loads((chain_dir / "chain.json").read_text(encoding="utf-8"))
    assert meta["pid"] == "proc-1"
    assert meta["cwd"] == "/tmp/proj"
    assert meta["generation"] == 1


def test_two_distinct_pids_create_two_chain_dirs(tmp_path):
    root = tmp_path / ".burnless"

    payload_a = recovery.write_handoff(root, host="claude", host_session_id="sid-a", process_instance_id="proc-a")
    payload_b = recovery.write_handoff(root, host="claude", host_session_id="sid-b", process_instance_id="proc-b")

    assert payload_a["chain_id"] != payload_b["chain_id"]
    chains_root = root / "epochs" / "_rolling" / "chains"
    assert len(list(chains_root.glob("*/chain.json"))) == 2


def test_same_pid_rewriting_reuses_same_chain_and_overwrites_handoff(tmp_path):
    root = tmp_path / ".burnless"

    first = recovery.write_handoff(root, host="claude", host_session_id="sid-1", process_instance_id="proc-x")
    second = recovery.write_handoff(root, host="claude", host_session_id="sid-1", process_instance_id="proc-x")

    assert first["chain_id"] == second["chain_id"]
    chains_root = root / "epochs" / "_rolling" / "chains"
    assert len(list(chains_root.glob("*/chain.json"))) == 1


def test_journal_append_envelope_carries_chain_id(tmp_path):
    root = tmp_path / ".burnless"

    envelope = {
        "host": "claude",
        "host_session_id": "sid-1",
        "process_instance_id": "proc-1",
        "cwd": "/tmp/proj",
        "exchange_id": "sha256:abc123",
    }
    record = recovery.journal_append(root, envelope)

    assert "chain_id" in record
    assert record["chain_id"]


def test_write_checkpoint_carries_chain_id(tmp_path):
    root = tmp_path / ".burnless"

    checkpoint = recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        living_md="# Foco atual\n",
        harvested_state={},
        applied_through=0,
    )

    assert "chain_id" in checkpoint
    assert checkpoint["chain_id"]
