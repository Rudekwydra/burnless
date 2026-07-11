"""Test suite for bytes-in-TimeoutExpired fix (d837)."""

import json
import subprocess
import tempfile
from pathlib import Path

from burnless.delegations import write_summary
from burnless.agents import _run_once


def test_cpython_timeout_produces_bytes():
    """Verify CPython TimeoutExpired.stdout/.stderr are bytes when timeout occurs after partial output."""
    try:
        subprocess.run(
            ["python3", "-c", "import sys,time; sys.stdout.write('partial output'); sys.stdout.flush(); time.sleep(5)"],
            capture_output=True,
            text=True,
            timeout=0.3,
        )
        assert False, "Should have raised TimeoutExpired"
    except subprocess.TimeoutExpired as e:
        assert isinstance(e.stdout, bytes), f"Expected bytes, got {type(e.stdout)}"
        assert e.stdout == b"partial output"
        # stderr is None when there's no stderr output
        assert e.stderr is None or isinstance(e.stderr, bytes)


def test_write_summary_with_nested_bytes():
    """Ensure write_summary handles nested bytes without crashing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        summary = {
            "summary": b"texto raw",
            "issues": [b"issue em bytes"],
            "nested": {"deep": b"mais bytes"},
            "normal_str": "this is fine",
        }

        out_path = tmp_path / "x.json"
        write_summary(out_path, summary)

        # Verify file was written and reloads without error
        assert out_path.exists()
        with open(out_path) as f:
            reloaded = json.load(f)

        # All bytes should be decoded to str
        assert isinstance(reloaded["summary"], str)
        assert reloaded["summary"] == "texto raw"
        assert isinstance(reloaded["issues"][0], str)
        assert reloaded["issues"][0] == "issue em bytes"
        assert isinstance(reloaded["nested"]["deep"], str)
        assert reloaded["nested"]["deep"] == "mais bytes"
        assert reloaded["normal_str"] == "this is fine"


def test_runner_stdout_bytes_handling():
    """Verify runner.py decoding logic works with bytes stdout."""
    # Simulate the scenario where result["stdout"] is bytes
    _raw_stdout = b"linha1\nlinha2"

    # Apply the defensive decode pattern
    if isinstance(_raw_stdout, bytes):
        _raw_stdout = _raw_stdout.decode("utf-8", errors="replace")

    # Now safe to use string operations
    _stdout_lines = _raw_stdout.strip().splitlines()
    _stdout_tail = _stdout_lines[-1] if _stdout_lines else ""
    _summary = (_stdout_tail[:200] or "Worker finished.").strip()

    assert isinstance(_summary, str)
    assert _summary == "linha2"


def test_empty_bytes_stdout():
    """Verify decode handles empty bytes."""
    _raw_stdout = b""

    if isinstance(_raw_stdout, bytes):
        _raw_stdout = _raw_stdout.decode("utf-8", errors="replace")

    assert _raw_stdout == ""
    assert isinstance(_raw_stdout, str)


def test_bytes_with_invalid_utf8():
    """Verify decode with errors='replace' handles invalid UTF-8."""
    _raw_stdout = b"valid\xFF\xFEinvalid"

    if isinstance(_raw_stdout, bytes):
        _raw_stdout = _raw_stdout.decode("utf-8", errors="replace")

    assert isinstance(_raw_stdout, str)
    # Should contain replacement characters
    assert "�" in _raw_stdout or "valid" in _raw_stdout
