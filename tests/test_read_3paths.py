"""QTP-D: burnless read fallback chain — capsule → temp → log → not found."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from burnless import cli, paths


def _setup_root(tmp_path: Path) -> Path:
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("logs", "temp", "capsules", "delegations", "chat"):
        p[key].mkdir(parents=True, exist_ok=True)
    return root


def _ns(did: str) -> argparse.Namespace:
    return argparse.Namespace(id=did)


def test_read_prefers_capsule_when_present(tmp_path: Path, capsys, monkeypatch):
    root = _setup_root(tmp_path)
    p = paths.paths_for(root)
    (p["capsules"] / "d001.json").write_text('{"id":"d001","kind":"capsule-final"}', encoding="utf-8")
    (p["temp"] / "d001.json").write_text('{"status":"OK"}', encoding="utf-8")  # should be ignored
    (p["logs"] / "d001.log").write_text("raw log content", encoding="utf-8")  # should be ignored

    monkeypatch.setattr(cli.paths_mod, "require_root", lambda: root)
    rc = cli.cmd_read(_ns("d001"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "capsule-final" in out
    assert "[capsule]" in out
    assert "OK" not in out  # temp summary should not be emitted
    assert "raw log content" not in out


def test_read_falls_back_to_temp_when_no_capsule(tmp_path: Path, capsys, monkeypatch):
    root = _setup_root(tmp_path)
    p = paths.paths_for(root)
    (p["temp"] / "d002.json").write_text(json.dumps({"id": "d002", "status": "PART", "issues": ["x"]}), encoding="utf-8")
    (p["logs"] / "d002.log").write_text("raw log content", encoding="utf-8")

    monkeypatch.setattr(cli.paths_mod, "require_root", lambda: root)
    rc = cli.cmd_read(_ns("d002"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "PART" in out
    assert "raw log content" not in out  # log not used when temp exists


def test_read_falls_back_to_log_when_no_capsule_no_temp(tmp_path: Path, capsys, monkeypatch):
    root = _setup_root(tmp_path)
    p = paths.paths_for(root)
    (p["logs"] / "d003.log").write_text("raw worker stdout here", encoding="utf-8")

    monkeypatch.setattr(cli.paths_mod, "require_root", lambda: root)
    rc = cli.cmd_read(_ns("d003"))
    captured = capsys.readouterr()
    assert rc == 0
    assert "raw worker stdout here" in captured.out
    assert "log fallback" in captured.err


def test_read_returns_2_when_nothing_exists(tmp_path: Path, capsys, monkeypatch):
    root = _setup_root(tmp_path)
    monkeypatch.setattr(cli.paths_mod, "require_root", lambda: root)
    rc = cli.cmd_read(_ns("d999"))
    captured = capsys.readouterr()
    assert rc == 2
    assert "no record of d999" in captured.err
