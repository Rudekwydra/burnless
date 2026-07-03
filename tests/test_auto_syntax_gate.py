import os
import tempfile

from burnless.exec.runner import _apply_syntax_gate


def _mk(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _base_summary(files):
    return {"status": "OK", "files_touched": files, "issues": [], "validated": []}


def test_valid_python_stays_ok(tmp_path):
    f = _mk(tmp_path, "good.py", "x = 1\n")
    log = tmp_path / "log.txt"
    out = _apply_syntax_gate(_base_summary([f]), cwd=str(tmp_path), did="d1", log_path=str(log), timeout=30)
    assert out["status"] == "OK"
    assert any("syntax: 1/1 files ok" in v for v in out["validated"])


def test_broken_python_demotes_to_part(tmp_path):
    f = _mk(tmp_path, "bad.py", "def broken(:\n")
    log = tmp_path / "log.txt"
    out = _apply_syntax_gate(_base_summary([f]), cwd=str(tmp_path), did="d1", log_path=str(log), timeout=30)
    assert out["status"] == "PART"
    assert any("syntax_failed" in i for i in out["issues"])


def test_noop_when_status_not_ok(tmp_path):
    f = _mk(tmp_path, "bad.py", "def broken(:\n")
    log = tmp_path / "log.txt"
    s = {"status": "PART", "files_touched": [f], "issues": [], "validated": []}
    out = _apply_syntax_gate(s, cwd=str(tmp_path), did="d1", log_path=str(log), timeout=30)
    assert out["status"] == "PART"


def test_noop_when_no_files(tmp_path):
    log = tmp_path / "log.txt"
    out = _apply_syntax_gate(_base_summary([]), cwd=str(tmp_path), did="d1", log_path=str(log), timeout=30)
    assert out["status"] == "OK"


def test_broken_shell_demotes_to_part(tmp_path):
    f = _mk(tmp_path, "bad.sh", "if [ 1 -eq 1 ]; then\n  echo hi\n")
    log = tmp_path / "log.txt"
    out = _apply_syntax_gate(_base_summary([f]), cwd=str(tmp_path), did="d1", log_path=str(log), timeout=30)
    assert out["status"] == "PART"


def test_valid_shell_stays_ok(tmp_path):
    f = _mk(tmp_path, "good.sh", "if [ 1 -eq 1 ]; then\n  echo hi\nfi\n")
    log = tmp_path / "log.txt"
    out = _apply_syntax_gate(_base_summary([f]), cwd=str(tmp_path), did="d1", log_path=str(log), timeout=30)
    assert out["status"] == "OK"


def test_broken_json_demotes_to_part(tmp_path):
    f = _mk(tmp_path, "bad.json", "{not valid json,}")
    log = tmp_path / "log.txt"
    out = _apply_syntax_gate(_base_summary([f]), cwd=str(tmp_path), did="d1", log_path=str(log), timeout=30)
    assert out["status"] == "PART"


def test_unknown_extension_ignored(tmp_path):
    f = _mk(tmp_path, "notes.md", "# not code, arbitrary ( unbalanced\n")
    log = tmp_path / "log.txt"
    out = _apply_syntax_gate(_base_summary([f]), cwd=str(tmp_path), did="d1", log_path=str(log), timeout=30)
    assert out["status"] == "OK"


def test_missing_file_skipped(tmp_path):
    log = tmp_path / "log.txt"
    ghost = str(tmp_path / "does_not_exist.py")
    out = _apply_syntax_gate(_base_summary([ghost]), cwd=str(tmp_path), did="d1", log_path=str(log), timeout=30)
    assert out["status"] == "OK"


def test_relative_path_resolved_against_cwd(tmp_path):
    _mk(tmp_path, "rel.py", "def broken(:\n")
    log = tmp_path / "log.txt"
    out = _apply_syntax_gate(_base_summary(["rel.py"]), cwd=str(tmp_path), did="d1", log_path=str(log), timeout=30)
    assert out["status"] == "PART"
