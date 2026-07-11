import json
import pytest
from burnless.integrity import check_run_integrity, scan_orphans


def _make_dirs(root):
    (root / ".burnless" / "delegations").mkdir(parents=True, exist_ok=True)
    (root / ".burnless" / "capsules").mkdir(parents=True, exist_ok=True)


def test_missing_capsule_warns(tmp_path):
    import os
    import time
    _make_dirs(tmp_path)
    (tmp_path / ".burnless" / "delegations" / "d999.md").write_text("# Delegation d999")
    (tmp_path / ".burnless" / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".burnless" / "logs" / "d999.log").write_text("log")
    old_time = time.time() - 2000
    os.utime(tmp_path / ".burnless" / "logs" / "d999.log", (old_time, old_time))
    warns = check_run_integrity("d999", tmp_path)
    assert any("capsule" in w.lower() for w in warns), warns
    assert "d999" in scan_orphans(tmp_path)


def test_unparseable_capsule_warns(tmp_path):
    _make_dirs(tmp_path)
    (tmp_path / ".burnless" / "delegations" / "d998.md").write_text("# Delegation d998")
    (tmp_path / ".burnless" / "capsules" / "d998.json").write_text("{bad")
    warns = check_run_integrity("d998", tmp_path)
    assert any("unparseable" in w.lower() for w in warns), warns


def test_clean_ok(tmp_path):
    _make_dirs(tmp_path)
    (tmp_path / ".burnless" / "delegations" / "d997.md").write_text("# Delegation d997")
    (tmp_path / ".burnless" / "capsules" / "d997.json").write_text(
        json.dumps({"status": "OK", "summary": "done"})
    )
    warns = check_run_integrity("d997", tmp_path)
    assert warns == []
