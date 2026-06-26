"""Phase 7B: runner emits an audit-graph record per delegation (helper contract)."""
from burnless.exec import runner
from burnless import audit_graph as ag


def test_emit_audit_record_writes(tmp_path):
    summary = {
        "status": "OK",
        "files_touched": ["/a/b.py"],
        "validated": ["verify: 2/2 checks passed"],
    }
    runner._emit_audit_record(tmp_path, "d99", summary, "/x/cap.json", "/x/log.txt", {})
    recs = ag.read_records(tmp_path, "d99")
    assert len(recs) == 1
    r = recs[0]
    assert r["status"] == "OK"
    assert r["worker_status"] == "OK"
    assert r["verify_status"] == "passed"
    assert r["files_declared"] == ["/a/b.py"]
    assert r["capsule_ref"] == "/x/cap.json"
    assert r["raw_log_ref"] == "/x/log.txt"
    assert r["created_at"]  # stamped


def test_emit_audit_record_verify_failed(tmp_path):
    summary = {"status": "PART", "validated": ["verify: 1/3 checks passed"]}
    runner._emit_audit_record(tmp_path, "d1", summary, "c", "l", {})
    recs = ag.read_records(tmp_path, "d1")
    assert len(recs) == 1
    assert recs[0]["verify_status"] == "failed"


def test_emit_audit_record_gated_off(tmp_path):
    runner._emit_audit_record(
        tmp_path, "d1", {"status": "OK"}, "c", "l", {"audit": {"graph_enabled": False}}
    )
    assert ag.read_records(tmp_path) == []


def test_emit_audit_record_fail_open_never_raises(tmp_path):
    # summary missing keys + weird types must not raise
    runner._emit_audit_record(tmp_path, "d1", {}, "c", "l", {})
    runner._emit_audit_record(tmp_path, "d2", {"validated": "not-a-list"}, "c", "l", {})
    recs = ag.read_records(tmp_path)
    assert len(recs) == 2
