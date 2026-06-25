import os
import time
from pathlib import Path

from burnless import debugless


def _mk(d: Path, name: str, age_hours: float) -> Path:
    p = d / name
    p.write_text("x", encoding="utf-8")
    t = time.time() - age_hours * 3600
    os.utime(p, (t, t))
    return p


def _setup(tmp_path: Path):
    deleg = tmp_path / "delegations"
    deleg.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    return deleg, logs


def test_select_newest_first_within_window(tmp_path):
    deleg, logs = _setup(tmp_path)
    for did, age in {"d001": 48, "d002": 10, "d003": 2, "d004": 1}.items():
        _mk(deleg, f"{did}.md", age)
        _mk(logs, f"{did}.log", age)
    ids = debugless._select_delegation_ids(tmp_path, since_hours=24, limit=2)
    assert ids == ["d004", "d003"], ids


def test_window_excludes_old(tmp_path):
    deleg, logs = _setup(tmp_path)
    _mk(deleg, "d001.md", 48)
    _mk(logs, "d001.log", 48)
    _mk(deleg, "d002.md", 2)
    _mk(logs, "d002.log", 2)
    ids = debugless._select_delegation_ids(tmp_path, since_hours=24, limit=10)
    assert ids == ["d002"], ids


def test_skips_missing_log(tmp_path):
    deleg, logs = _setup(tmp_path)
    _mk(deleg, "d001.md", 1)
    _mk(deleg, "d002.md", 2)
    _mk(logs, "d002.log", 2)
    ids = debugless._select_delegation_ids(tmp_path, since_hours=24, limit=10)
    assert ids == ["d002"], ids
