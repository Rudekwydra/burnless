import os
import subprocess
import sys
import json
import time
import pytest
from burnless.recovery import _pid_is_dead_or_reused, _process_name_best_effort, gc_dead_chains, write_checkpoint, write_handoff, CHAIN_META_NAME


def test_reused_pid_treated_as_dead():
    """pid = str(os.getpid()) (vivo), expected_proc_name = proc-que-nao-existe-xyz => True."""
    pid = str(os.getpid())
    expected_proc_name = "proc-que-nao-existe-xyz"
    assert _pid_is_dead_or_reused(pid, expected_proc_name) is True


def test_same_proc_name_alive():
    """pid = str(os.getpid()), expected = _process_name_best_effort(str(os.getpid())) (não-vazio) => False."""
    pid = str(os.getpid())
    expected = _process_name_best_effort(pid)
    assert expected != "", "Process name should not be empty for current process"
    assert _pid_is_dead_or_reused(pid, expected) is False


def test_empty_expected_name_backcompat():
    """pid = str(os.getpid()), expected = "" => False (backcompat, assume vivo)."""
    pid = str(os.getpid())
    assert _pid_is_dead_or_reused(pid, "") is False


def test_dead_pid_still_dead():
    """subprocess sys.executable -c pass com wait() concluído; pid dele, expected qualquer => True."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc_pid = str(proc.pid)
    proc.wait()
    assert _pid_is_dead_or_reused(proc_pid, "any-proc-name") is True


def test_host_prefixed_pid():
    """pid = f"host-{os.getpid()}", expected = "proc-que-nao-existe-xyz" => True (prova _extract_os_pid)."""
    pid = f"host-{os.getpid()}"
    expected_proc_name = "proc-que-nao-existe-xyz"
    assert _pid_is_dead_or_reused(pid, expected_proc_name) is True


def test_gc_archives_uuid_pid_after_ttl(tmp_path):
    """Chain com UUID pid, last_seen 8 dias no passado => arquivada por GC."""
    root = tmp_path / ".burnless"
    uuid_pid = "deadbeef-0000-4000-8000-000000000000"
    write_checkpoint(
        root,
        host="claude",
        host_session_id="uuid-test-sid",
        process_instance_id=uuid_pid,
        living_md="## Test\n- uuid pid chain\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    write_handoff(root, host="claude", host_session_id="uuid-test-sid", process_instance_id=uuid_pid)
    chains_dir = root / "epochs" / "_rolling" / "chains"
    chain_meta_path = None
    for meta_path in chains_dir.glob("*/" + CHAIN_META_NAME):
        chain_meta_path = meta_path
        break
    assert chain_meta_path is not None
    meta = json.loads(chain_meta_path.read_text(encoding="utf-8"))
    old_last_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 86400))
    meta["last_seen"] = old_last_seen
    chain_meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    chain_id = chain_meta_path.parent.name

    result = gc_dead_chains(root, host="claude")

    assert result["archived"] == [chain_id]
    assert not (chains_dir / chain_id).exists()
    assert (chains_dir / "_archived" / chain_id / CHAIN_META_NAME).exists()


def test_gc_keeps_uuid_pid_fresh(tmp_path):
    """Chain com UUID pid, last_seen = agora => NÃO arquivada (lista archived vazia)."""
    root = tmp_path / ".burnless"
    uuid_pid = "deadbeef-0000-4000-8000-000000000001"
    write_checkpoint(
        root,
        host="claude",
        host_session_id="uuid-fresh-sid",
        process_instance_id=uuid_pid,
        living_md="## Test\n- uuid pid chain fresh\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    write_handoff(root, host="claude", host_session_id="uuid-fresh-sid", process_instance_id=uuid_pid)
    chains_dir = root / "epochs" / "_rolling" / "chains"
    chain_meta_path = None
    for meta_path in chains_dir.glob("*/" + CHAIN_META_NAME):
        chain_meta_path = meta_path
        break
    assert chain_meta_path is not None
    chain_id = chain_meta_path.parent.name

    result = gc_dead_chains(root, host="claude")

    assert result["archived"] == []
    assert (chains_dir / chain_id).exists()
