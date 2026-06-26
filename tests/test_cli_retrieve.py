from __future__ import annotations

import json
import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from burnless import cli
from burnless import retrieve as retrieve_mod
from burnless import events as events_mod
from burnless import config as config_mod
from burnless import paths as paths_mod


@pytest.fixture
def fake_burnless_root(tmp_path):
    """Create a fake .burnless directory structure for testing."""
    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)

    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    config_file = config_dir / "config.yaml"
    config_file.write_text("privacy:\n  raw_retention: keep\n")

    retrieve_dir = root / "retrieve"
    retrieve_dir.mkdir(parents=True, exist_ok=True)

    return root


@pytest.fixture
def seeded_index(fake_burnless_root):
    """Seed the index with test records."""
    root = fake_burnless_root

    capsule_rec = retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="capsule",
        capsule_ref="d001.json",
        entities=["entity1"],
        files=["file1.py"],
        status="ok",
        content="This is a capsule content for testing.",
    )

    worker_log_rec = retrieve_mod.index_record(
        root,
        delegation_id="d002",
        kind="worker_log",
        raw_ref="/tmp/worker.log",
        status="ok",
        content="Worker log content here.",
    )

    return root, capsule_rec, worker_log_rec


def test_cmd_retrieve_with_delegation_id(seeded_index, capsys):
    """Test cmd_retrieve with a delegation_id argument."""
    root, capsule_rec, worker_log_rec = seeded_index

    with patch("burnless.paths.require_root", return_value=root):
        with patch("burnless.config.load") as mock_load:
            mock_load.return_value = {"privacy": {"raw_retention": "keep"}}
            args = argparse.Namespace(
                id="d001",
                query=None,
                file=None,
                entity=None,
                json=True,
                full=False,
            )
            result = cli.cmd_retrieve(args)

    assert result == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["count"] == 1
    assert len(output["results"]) == 1
    assert output["results"][0]["ref_id"] == capsule_rec["ref_id"]


def test_cmd_retrieve_writes_event(seeded_index):
    """Test that cmd_retrieve writes a retrieve_called event."""
    root, _, _ = seeded_index

    with patch("burnless.paths.require_root", return_value=root):
        with patch("burnless.config.load") as mock_load:
            mock_load.return_value = {"privacy": {"raw_retention": "keep"}}
            args = argparse.Namespace(
                id="d001",
                query=None,
                file=None,
                entity=None,
                json=True,
                full=False,
            )
            cli.cmd_retrieve(args)

    events = events_mod.read_events(root, event_type="retrieve_called")
    assert len(events) > 0
    assert events[-1]["event_type"] == "retrieve_called"
    assert events[-1]["data"]["id"] == "d001"


def test_cmd_retrieve_privacy_gate(fake_burnless_root, capsys):
    """Test that cmd_retrieve respects privacy.raw_retention == none."""
    root = fake_burnless_root

    config_file = root / "config" / "config.yaml"
    config_file.write_text("privacy:\n  raw_retention: none\n")

    with patch("burnless.paths.require_root", return_value=root):
        with patch("burnless.config.load") as mock_load:
            mock_load.return_value = {"privacy": {"raw_retention": "none"}}
            args = argparse.Namespace(
                id="d001",
                query=None,
                file=None,
                entity=None,
                json=True,
                full=False,
            )
            result = cli.cmd_retrieve(args)

    assert result == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["error"] == "raw_retention_disabled"
    assert output["capsule_available"] is True


def test_cmd_retrieve_no_matches(fake_burnless_root, capsys):
    """Test cmd_retrieve with no matching records."""
    root = fake_burnless_root

    with patch("burnless.paths.require_root", return_value=root):
        with patch("burnless.config.load") as mock_load:
            mock_load.return_value = {"privacy": {"raw_retention": "keep"}}
            args = argparse.Namespace(
                id="nonexistent",
                query=None,
                file=None,
                entity=None,
                json=False,
                full=False,
            )
            result = cli.cmd_retrieve(args)

    assert result == 0
    captured = capsys.readouterr()
    assert "no matches" in captured.out


def test_cmd_retrieve_human_output(seeded_index, capsys):
    """Test cmd_retrieve human-readable output format."""
    root, capsule_rec, _ = seeded_index

    with patch("burnless.paths.require_root", return_value=root):
        with patch("burnless.config.load") as mock_load:
            mock_load.return_value = {"privacy": {"raw_retention": "keep"}}
            args = argparse.Namespace(
                id="d001",
                query=None,
                file=None,
                entity=None,
                json=False,
                full=False,
            )
            result = cli.cmd_retrieve(args)

    assert result == 0
    captured = capsys.readouterr()
    assert "[capsule]" in captured.out
    assert "This is a capsule content" in captured.out


def test_cmd_search_capsules_returns_only_capsules(seeded_index, capsys):
    """Test cmd_search_capsules returns only kind==capsule records."""
    root, capsule_rec, worker_log_rec = seeded_index

    with patch("burnless.paths.require_root", return_value=root):
        args = argparse.Namespace(
            query="",
            limit=10,
            json=True,
        )
        result = cli.cmd_search_capsules(args)

    assert result == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["count"] == 1
    assert all(r["kind"] == "capsule" for r in output["results"])


def test_cmd_search_capsules_limit(seeded_index, capsys):
    """Test cmd_search_capsules respects the limit argument."""
    root, _, _ = seeded_index

    retrieve_mod.index_record(
        root,
        delegation_id="d003",
        kind="capsule",
        capsule_ref="d003.json",
        content="Another capsule",
    )
    retrieve_mod.index_record(
        root,
        delegation_id="d004",
        kind="capsule",
        capsule_ref="d004.json",
        content="Yet another capsule",
    )

    with patch("burnless.paths.require_root", return_value=root):
        args = argparse.Namespace(
            query="",
            limit=2,
            json=True,
        )
        result = cli.cmd_search_capsules(args)

    assert result == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["count"] <= 2


def test_cmd_search_capsules_human_output(seeded_index, capsys):
    """Test cmd_search_capsules human-readable output format."""
    root, capsule_rec, _ = seeded_index

    with patch("burnless.paths.require_root", return_value=root):
        args = argparse.Namespace(
            query="",
            limit=10,
            json=False,
        )
        result = cli.cmd_search_capsules(args)

    assert result == 0
    captured = capsys.readouterr()
    assert " -> " in captured.out
    assert "d001" in captured.out


def test_cmd_search_capsules_no_matches(fake_burnless_root, capsys):
    """Test cmd_search_capsules with no matches."""
    root = fake_burnless_root

    with patch("burnless.paths.require_root", return_value=root):
        args = argparse.Namespace(
            query="nonexistent",
            limit=10,
            json=False,
        )
        result = cli.cmd_search_capsules(args)

    assert result == 0
    captured = capsys.readouterr()
    assert "no matches" in captured.out
