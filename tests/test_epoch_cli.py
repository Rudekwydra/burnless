from __future__ import annotations

import argparse
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from burnless import cli, epochs


@pytest.fixture
def tmp_root():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def mock_summarizer(monkeypatch):
    def fake_summarizer(pr):
        return lambda text: "SUM:" + text[:20]
    monkeypatch.setattr(epochs, "epoch_summarizer", fake_summarizer)


def test_capture_appends(tmp_root, mock_summarizer, capsys):
    """Test that capture reads from stdin, summarizes, and appends to epoch."""
    args = argparse.Namespace(
        epoch_cmd="capture",
        chat_id="c1",
        root=str(tmp_root),
    )
    with patch("sys.stdin", io.StringIO("turn 1 pediu X fez Y ref a.py")):
        rc = cli.cmd_epoch(args)
    assert rc == 0
    captured = capsys.readouterr()
    slot_name = captured.out.strip()
    assert slot_name == "001.md"

    slot_path = tmp_root / ".burnless" / "epochs" / "c1" / "001.md"
    assert slot_path.exists()
    content = slot_path.read_text(encoding='utf-8')
    assert "SUM:" in content
    assert "turn 1" in content


def test_capture_consolidates_at_10(tmp_root, mock_summarizer, capsys):
    """Test that after 10 captures, consolidation happens (a01.md created, originals moved)."""
    for i in range(10):
        args = argparse.Namespace(
            epoch_cmd="capture",
            chat_id="c1",
            root=str(tmp_root),
        )
        text = f"turn {i} pediu X fez Y ref a.py"
        with patch("sys.stdin", io.StringIO(text)):
            rc = cli.cmd_epoch(args)
        assert rc == 0

    capsys.readouterr()

    d = tmp_root / ".burnless" / "epochs" / "c1"
    assert (d / "a01.md").exists(), "consolidation at 10 failed"

    for i in range(1, 11):
        slot = f"{i:03d}.md"
        assert (d / "originais" / slot).exists(), f"{slot} not moved to originais"
        assert not (d / slot).exists(), f"{slot} still in root after consolidation"


def test_read_returns_active_chain(tmp_root, mock_summarizer, capsys):
    """Test that read outputs the active chain contents."""
    for i in range(3):
        args = argparse.Namespace(
            epoch_cmd="capture",
            chat_id="c1",
            root=str(tmp_root),
        )
        text = f"content {i}"
        with patch("sys.stdin", io.StringIO(text)):
            cli.cmd_epoch(args)

    capsys.readouterr()

    args = argparse.Namespace(
        epoch_cmd="read",
        chat_id="c1",
        root=str(tmp_root),
    )
    rc = cli.cmd_epoch(args)
    assert rc == 0
    captured = capsys.readouterr()
    output = captured.out
    assert "001.md" in output
    assert "002.md" in output
    assert "003.md" in output
    assert "SUM:" in output


def test_cleanup(tmp_root, mock_summarizer, capsys):
    """Test that cleanup removes originais and prints the count."""
    for i in range(10):
        args = argparse.Namespace(
            epoch_cmd="capture",
            chat_id="c1",
            root=str(tmp_root),
        )
        with patch("sys.stdin", io.StringIO(f"turn {i}")):
            cli.cmd_epoch(args)

    capsys.readouterr()

    args = argparse.Namespace(
        epoch_cmd="cleanup",
        chat_id="c1",
        root=str(tmp_root),
    )
    rc = cli.cmd_epoch(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "removed" in captured.out
    assert "10" in captured.out

    d = tmp_root / ".burnless" / "epochs" / "c1"
    assert not (d / "originais").exists(), "originais dir not removed"


def test_capture_fail_open(tmp_root, monkeypatch, capsys):
    """Test that if summarizer returns None, capture still returns 0 (fail-open)."""
    def mock_summarizer_none(pr):
        return lambda text: None
    monkeypatch.setattr(epochs, "epoch_summarizer", mock_summarizer_none)

    args = argparse.Namespace(
        epoch_cmd="capture",
        chat_id="c1",
        root=str(tmp_root),
    )
    with patch("sys.stdin", io.StringIO("some text")):
        rc = cli.cmd_epoch(args)

    assert rc == 0
    captured = capsys.readouterr()
    assert "warning" in captured.err
    assert "fail-open" in captured.err

    d = tmp_root / ".burnless" / "epochs" / "c1"
    assert not (d / "001.md").exists(), "file written despite None summarizer"
