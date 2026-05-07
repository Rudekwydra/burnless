"""QTP-A/B: filesystem-first auditor for kind=execution + status precedence."""
from __future__ import annotations

from pathlib import Path

import pytest

from burnless import cli


def test_returns_none_when_no_files_touched(tmp_path: Path):
    summary = {"status": "OK", "kind": "execution", "files_touched": []}
    assert cli._audit_execution_filesystem(summary, tmp_path) is None


def test_returns_none_when_files_touched_not_a_list(tmp_path: Path):
    summary = {"status": "OK", "kind": "execution", "files_touched": "single.txt"}
    assert cli._audit_execution_filesystem(summary, tmp_path) is None


def test_ok_when_all_files_present_no_validated(tmp_path: Path):
    (tmp_path / "a.py").write_text("print('a')")
    (tmp_path / "b.py").write_text("print('b')")
    summary = {
        "status": "OK", "kind": "execution",
        "files_touched": [str(tmp_path / "a.py"), str(tmp_path / "b.py")],
        "validated": [],
    }
    audit = cli._audit_execution_filesystem(summary, tmp_path)
    assert audit is not None
    assert audit["status"] == "OK"
    assert audit["auditor_name"] == "filesystem_first"
    assert "2 file(s) present" in audit["summary"]


def test_fail_when_one_file_missing(tmp_path: Path):
    (tmp_path / "a.py").write_text("ok")
    summary = {
        "status": "OK", "kind": "execution",
        "files_touched": [str(tmp_path / "a.py"), str(tmp_path / "missing.py")],
    }
    audit = cli._audit_execution_filesystem(summary, tmp_path)
    assert audit is not None
    assert audit["status"] == "FAIL"
    assert any("missing" in i for i in audit["issues"])
    assert "missing.py" in audit["issues"][0]


def test_relative_paths_resolved_against_cwd(tmp_path: Path):
    (tmp_path / "out.txt").write_text("data")
    summary = {
        "status": "OK", "kind": "execution",
        "files_touched": ["out.txt"],
    }
    audit = cli._audit_execution_filesystem(summary, tmp_path)
    assert audit is not None
    assert audit["status"] == "OK"


def test_fail_when_validated_size_mismatches_actual(tmp_path: Path):
    (tmp_path / "report.pdf").write_bytes(b"x" * 1000)  # 1KB
    summary = {
        "status": "OK", "kind": "execution",
        "files_touched": [str(tmp_path / "report.pdf")],
        "validated": ["report.pdf 537629 bytes"],  # claimed 538KB but is 1KB
    }
    audit = cli._audit_execution_filesystem(summary, tmp_path)
    assert audit is not None
    assert audit["status"] == "FAIL"
    assert "size mismatch" in audit["summary"]
    assert any("report.pdf" in i for i in audit["issues"])


def test_ok_when_validated_size_within_tolerance(tmp_path: Path):
    (tmp_path / "report.pdf").write_bytes(b"x" * 100000)  # 100000 bytes
    summary = {
        "status": "OK", "kind": "execution",
        "files_touched": [str(tmp_path / "report.pdf")],
        "validated": ["report.pdf 100500 bytes"],  # off by 500B (within 1024B tolerance)
    }
    audit = cli._audit_execution_filesystem(summary, tmp_path)
    assert audit is not None
    assert audit["status"] == "OK"


def test_validated_unparseable_entries_ignored(tmp_path: Path):
    (tmp_path / "out.txt").write_text("hello")
    summary = {
        "status": "OK", "kind": "execution",
        "files_touched": [str(tmp_path / "out.txt")],
        "validated": ["everything looks fine", "no errors observed"],  # no size pattern
    }
    audit = cli._audit_execution_filesystem(summary, tmp_path)
    assert audit is not None
    assert audit["status"] == "OK"  # unparseable entries don't cause FAIL


def test_status_precedence_qtp_b_no_downgrade_when_fs_says_ok(tmp_path: Path):
    """QTP-B: when filesystem audit OK, runner shouldn't override status to PART."""
    (tmp_path / "a.py").write_text("print('a')")
    summary = {
        "status": "OK", "kind": "execution",
        "files_touched": [str(tmp_path / "a.py")],
        "validated": [],
    }
    audit = cli._audit_execution_filesystem(summary, tmp_path)
    assert audit["status"] == "OK"
    # Caller would set summary["audit"] = audit but NOT downgrade summary["status"]
    # since fs_audit is OK. The wiring in _audit_summary_evidence enforces this.
    assert audit.get("issues") == []
