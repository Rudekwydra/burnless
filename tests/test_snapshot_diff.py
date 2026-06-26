import shutil
import subprocess

import pytest

from burnless import integrity, audit_graph
from burnless.exec import runner


GIT_AVAILABLE = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not GIT_AVAILABLE, reason="git not available")


def _init_repo(path):
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.test"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True, check=True)
    (path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True, check=True)


@requires_git
def test_snapshot_tree_clean_repo(tmp_path):
    _init_repo(tmp_path)
    snap = integrity.snapshot_tree(tmp_path)
    assert snap["head"] != ""
    assert snap["porcelain"] == {}


@requires_git
def test_snapshot_tree_detects_new_file(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("x\n")
    snap = integrity.snapshot_tree(tmp_path)
    assert "new.txt" in snap["porcelain"]
    assert snap["porcelain"]["new.txt"][0] == "?"


@requires_git
def test_diff_snapshots_new_file(tmp_path):
    _init_repo(tmp_path)
    before = integrity.snapshot_tree(tmp_path)
    (tmp_path / "added.txt").write_text("hello\n")
    after = integrity.snapshot_tree(tmp_path)
    diff = integrity.diff_snapshots(before, after, tmp_path)
    assert "added.txt" in diff["files_changed"]
    assert "added.txt" in diff["added"]


@requires_git
def test_diff_snapshots_no_change(tmp_path):
    _init_repo(tmp_path)
    before = integrity.snapshot_tree(tmp_path)
    after = integrity.snapshot_tree(tmp_path)
    diff = integrity.diff_snapshots(before, after, tmp_path)
    assert diff["files_changed"] == []


def test_snapshot_tree_fail_open_non_git(tmp_path):
    snap = integrity.snapshot_tree(tmp_path)
    assert snap == {"head": "", "porcelain": {}}


def test_build_record_carries_snapshot_fields():
    rec = audit_graph.build_record(
        delegation_id="d1",
        status="OK",
        files_changed=["/a"],
        diff_stats={"files": 1, "insertions": 3, "deletions": 0},
        suspicious=True,
    )
    assert rec["files_changed"] == ["/a"]
    assert rec["diff_stats"] == {"files": 1, "insertions": 3, "deletions": 0}
    assert rec["suspicious"] is True
    assert rec["schema_version"] == 1


def test_emit_audit_record_marks_suspicious_passthrough(tmp_path):
    summary = {
        "status": "OK",
        "suspicious": True,
        "files_changed": [],
        "diff_stats": {},
    }
    capsule_path = tmp_path / ".burnless" / "capsules" / "d1.json"
    log_path = tmp_path / "d1.log"
    runner._emit_audit_record(tmp_path, "d1", summary, capsule_path, log_path, {})
    records = audit_graph.read_records(tmp_path, delegation_id="d1")
    assert len(records) == 1
    assert records[0]["suspicious"] is True
