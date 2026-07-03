from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from burnless import cli, audit_graph


@pytest.fixture
def tmp_root():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def tmp_project_with_audit(tmp_root):
    """Create a temp project with .burnless dir and seed audit records."""
    burnless_dir = tmp_root / ".burnless"
    burnless_dir.mkdir(parents=True, exist_ok=True)

    record = audit_graph.build_record(
        delegation_id="d42",
        status="OK",
        verify_status="passed",
        files_declared=["/a/b.py"],
    )
    audit_graph.append_record(tmp_root, record)

    return tmp_root


def test_audit_text_mode_contains_delegation_id(tmp_project_with_audit, capsys, monkeypatch):
    """Test that text mode output contains the delegation ID."""
    burnless_root = tmp_project_with_audit / ".burnless"
    monkeypatch.setattr("burnless.paths.require_root", lambda: burnless_root)

    args = argparse.Namespace(
        delegation_id="d42",
        session=False,
        json=False,
    )
    rc = cli.cmd_audit(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "d42" in captured.out
    assert "OK" in captured.out


def test_audit_json_mode_parses(tmp_project_with_audit, capsys, monkeypatch):
    """Test that JSON mode emits valid JSON with records."""
    burnless_root = tmp_project_with_audit / ".burnless"
    monkeypatch.setattr("burnless.paths.require_root", lambda: burnless_root)

    args = argparse.Namespace(
        delegation_id="d42",
        session=False,
        json=True,
    )
    rc = cli.cmd_audit(args)
    assert rc == 0
    captured = capsys.readouterr()

    records = json.loads(captured.out)
    assert isinstance(records, list)
    assert len(records) == 1
    assert records[0]["delegation_id"] == "d42"
    assert records[0]["status"] == "OK"


def test_audit_session_mode_returns_all(tmp_project_with_audit, capsys, monkeypatch):
    """Test that --session returns all records."""
    burnless_root = tmp_project_with_audit / ".burnless"
    monkeypatch.setattr("burnless.paths.require_root", lambda: burnless_root)

    # Add another record
    record2 = audit_graph.build_record(
        delegation_id="d43",
        status="FAIL",
        verify_status="",
        files_declared=[],
    )
    audit_graph.append_record(tmp_project_with_audit, record2)

    args = argparse.Namespace(
        delegation_id=None,
        session=True,
        json=False,
    )
    rc = cli.cmd_audit(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "d42" in captured.out
    assert "d43" in captured.out


def test_audit_session_mode_includes_usage_summary(tmp_project_with_audit, capsys, monkeypatch):
    """Session audit should surface real usage when spend.jsonl is present."""
    burnless_root = tmp_project_with_audit / ".burnless"
    monkeypatch.setattr("burnless.paths.require_root", lambda: burnless_root)

    spend_path = burnless_root / "spend.jsonl"
    spend_path.write_text(
        json.dumps(
            {
                "ts": "2026-07-02T00:00:00Z",
                "delegation_id": "d42",
                "tier": "silver",
                "provider": "claude",
                "model": "claude-sonnet",
                "usage": {"input_tokens": 4, "output_tokens": 8},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    args = argparse.Namespace(
        delegation_id=None,
        session=True,
        json=False,
    )
    rc = cli.cmd_audit(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "usage real:" in captured.out
    assert "silver" in captured.out
    assert "claude" in captured.out


def test_audit_session_json_returns_all(tmp_project_with_audit, capsys, monkeypatch):
    """Test that --session --json returns all records as JSON."""
    burnless_root = tmp_project_with_audit / ".burnless"
    monkeypatch.setattr("burnless.paths.require_root", lambda: burnless_root)

    record2 = audit_graph.build_record(
        delegation_id="d43",
        status="FAIL",
        verify_status="",
        files_declared=[],
    )
    audit_graph.append_record(tmp_project_with_audit, record2)

    args = argparse.Namespace(
        delegation_id=None,
        session=True,
        json=True,
    )
    rc = cli.cmd_audit(args)
    assert rc == 0
    captured = capsys.readouterr()

    records = json.loads(captured.out)
    assert len(records) == 2
    ids = {r["delegation_id"] for r in records}
    assert ids == {"d42", "d43"}


def test_audit_no_records(tmp_root, capsys, monkeypatch):
    """Test that 'no audit records' is printed when none exist."""
    burnless_dir = tmp_root / ".burnless"
    burnless_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("burnless.paths.require_root", lambda: burnless_dir)
    monkeypatch.setattr("burnless.paths.paths_for", lambda root: {"temp": root / "temp"})

    args = argparse.Namespace(
        delegation_id="d99",
        session=False,
        json=False,
    )
    rc = cli.cmd_audit(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "no audit records" in captured.out
