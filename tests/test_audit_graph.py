import json
import pytest
from pathlib import Path
from burnless.audit_graph import (
    SCHEMA_VERSION,
    build_record,
    audit_graph_path,
    append_record,
    read_records,
    hash_file,
    render_one,
    render,
)


def test_build_record_has_full_schema():
    record = build_record(delegation_id="d1", status="OK")
    assert record["schema_version"] == 1
    assert record["delegation_id"] == "d1"
    assert record["status"] == "OK"
    assert record["worker_status"] == ""
    assert record["audit_status"] == ""
    assert record["verify_status"] == ""
    assert record["commands"] == []
    assert record["files_declared"] == []
    assert record["files_verified"] == []
    assert record["file_hashes_before"] == {}
    assert record["file_hashes_after"] == {}
    assert record["tests_declared"] == []
    assert record["tests_seen"] == []
    assert record["capsule_ref"] == ""
    assert record["raw_log_ref"] == ""
    assert record["created_at"] == ""


def test_build_record_with_all_params():
    record = build_record(
        delegation_id="d2",
        status="PART",
        worker_status="ERR",
        audit_status="FAILED",
        verify_status="failed",
        commands=["cmd1", "cmd2"],
        files_declared=["/a", "/b"],
        files_verified=["/a"],
        file_hashes_before={"/a": "sha256:abc123"},
        file_hashes_after={"/a": "sha256:def456"},
        tests_declared=["test1"],
        tests_seen=["test1"],
        capsule_ref="/path/capsule.json",
        raw_log_ref="/path/log",
        created_at="2026-06-26T10:00:00",
    )
    assert record["delegation_id"] == "d2"
    assert record["status"] == "PART"
    assert record["commands"] == ["cmd1", "cmd2"]
    assert record["files_declared"] == ["/a", "/b"]
    assert record["created_at"] == "2026-06-26T10:00:00"
    assert record["file_hashes_before"] == {"/a": "sha256:abc123"}


def test_append_and_read_roundtrip(tmp_path):
    rec1 = build_record(delegation_id="d1", status="OK")
    rec2 = build_record(delegation_id="d2", status="PART")

    assert append_record(str(tmp_path), rec1) is True
    assert append_record(str(tmp_path), rec2) is True

    records = read_records(str(tmp_path))
    assert len(records) == 2
    assert records[0]["delegation_id"] == "d1"
    assert records[1]["delegation_id"] == "d2"

    filtered = read_records(str(tmp_path), delegation_id="d1")
    assert len(filtered) == 1
    assert filtered[0]["delegation_id"] == "d1"


def test_read_missing_returns_empty(tmp_path):
    records = read_records(str(tmp_path))
    assert records == []


def test_read_skips_malformed_line(tmp_path):
    path = audit_graph_path(str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)

    valid = build_record(delegation_id="d1", status="OK")
    with open(path, "w") as f:
        f.write(json.dumps(valid) + "\n")
        f.write("this is garbage\n")
        f.write(json.dumps(valid) + "\n")

    records = read_records(str(tmp_path))
    assert len(records) == 2
    assert all(r["delegation_id"] == "d1" for r in records)


def test_hash_file_respects_max_bytes(tmp_path):
    small_file = tmp_path / "small.txt"
    small_file.write_text("hello")

    h = hash_file(str(small_file))
    assert h is not None
    assert h.startswith("sha256:")
    assert len(h) == 23

    h_limited = hash_file(str(small_file), max_bytes=1)
    assert h_limited is None

    h_missing = hash_file(str(tmp_path / "nonexistent.txt"))
    assert h_missing is None


def test_hash_file_large_file(tmp_path):
    large_file = tmp_path / "large.txt"
    large_file.write_bytes(b"x" * 6_000_000)

    h = hash_file(str(large_file), max_bytes=5_000_000)
    assert h is None

    h_unlim = hash_file(str(large_file), max_bytes=7_000_000)
    assert h_unlim is not None


def test_append_record_fail_open(tmp_path):
    record = build_record(delegation_id="d1", status="OK")

    result = append_record(str(tmp_path), record)
    assert result is True

    result_bad = append_record(str(tmp_path), {"not": "serializable", "object": object()})
    assert result_bad is False


def test_render_one_tolerates_missing_keys():
    record = {"delegation_id": "d9", "status": "PART"}
    rendered = render_one(record)
    assert "d9" in rendered
    assert "PART" in rendered
    assert len(rendered) > 0


def test_render_one_with_files_and_commands():
    record = build_record(
        delegation_id="d123",
        status="OK",
        verify_status="passed",
        files_declared=["/a", "/b"],
        files_verified=["/a", "/b"],
        commands=["cmd1"],
    )
    rendered = render_one(record)
    assert "d123" in rendered
    assert "OK" in rendered
    assert "verify:passed" in rendered
    assert "files 2/2" in rendered
    assert "cmds 1" in rendered


def test_render_empty_list():
    assert render([]) == ""


def test_render_multiple():
    rec1 = build_record(delegation_id="d1", status="OK")
    rec2 = build_record(delegation_id="d2", status="PART")
    rendered = render([rec1, rec2])
    assert "d1" in rendered
    assert "d2" in rendered
    lines = rendered.split("\n")
    assert len(lines) == 2
