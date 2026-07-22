import argparse
import tempfile
from pathlib import Path

from burnless.codex_integration import (
    write_or_update,
    remove_block,
    render_block,
    BLOCK_START,
    BLOCK_END,
)
from burnless.cli import _cmd_setup_codex


def test_render_block_contains_key_guidance():
    block = render_block("0.9.0")
    assert "burnless do" in block
    assert "absolute" in block
    assert "RECOMMENDATION" in block
    assert "not an enforced requirement" in block


def test_write_or_update_creates_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "AGENTS.md"
        action = write_or_update(path, "0.9.0")
        assert action == "created"
        content = path.read_text()
        assert BLOCK_START in content
        assert BLOCK_END in content


def test_write_or_update_updates_existing_block():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "AGENTS.md"
        write_or_update(path, "0.9.0")
        before = path.read_text()
        assert "v0.9.0" in before

        action = write_or_update(path, "0.9.1")
        assert action == "updated"
        after = path.read_text()
        assert after.count(BLOCK_START) == 1
        assert "v0.9.1" in after
        assert "v0.9.0" not in after


def test_write_or_update_appends_preserving_user_content():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "AGENTS.md"
        path.write_text("# My Codex notes\n\nSome real user prose here.\n")
        action = write_or_update(path, "0.9.0")
        assert action == "appended"
        content = path.read_text()
        assert "Some real user prose here." in content
        assert BLOCK_START in content
        assert content.index("Some real user prose here.") < content.index(BLOCK_START)


def test_remove_block():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "AGENTS.md"
        write_or_update(path, "0.9.0")
        assert BLOCK_START in path.read_text()

        removed = remove_block(path)
        assert removed is True
        content = path.read_text()
        assert BLOCK_START not in content

        removed_again = remove_block(path)
        assert removed_again is False


def test_cmd_setup_codex_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    args = argparse.Namespace(codex=True, dry_run=True)
    rc = _cmd_setup_codex(args)
    assert rc == 0
    agents_md = tmp_path / ".codex" / "AGENTS.md"
    assert not agents_md.exists()
    out = capsys.readouterr().out
    assert out.strip() != ""


def test_cmd_setup_codex_writes_when_not_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    args = argparse.Namespace(codex=True, dry_run=False)
    rc = _cmd_setup_codex(args)
    assert rc == 0
    agents_md = tmp_path / ".codex" / "AGENTS.md"
    assert agents_md.exists()
    assert BLOCK_START in agents_md.read_text()


def _make_burnless_root(base: Path) -> Path:
    """Minimal valid .burnless/ so unrelated bands (B, etc.) don't FAIL and
    pollute the exit-code assertion below — mirrors tests/test_doctor.py."""
    import yaml

    bl = base / ".burnless"
    bl.mkdir(parents=True, exist_ok=True)
    cfg = {
        "agents": {
            "bronze": {"name": "haiku", "command": "claude --model haiku -p"},
            "silver": {"name": "sonnet", "command": "claude --model sonnet -p"},
        },
        "routing": {"bronze": ["summarize"], "silver": ["code", "bug"]},
        "metrics": {"token_estimation_ratio": 4},
    }
    (bl / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    (bl / "state.json").write_text("{}")
    return bl


def test_doctor_codex_flag_reports_without_hard_failure(tmp_path, monkeypatch):
    from burnless import doctor as doctor_mod

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    _make_burnless_root(tmp_path)

    checks = doctor_mod.run_checks(home=tmp_path, cwd=tmp_path, codex=True)

    codex_checks = [c for c in checks if c.band == "I"]
    assert codex_checks
    # codex not installed/configured here — every check in this band must
    # stay informational (PASS/WARN), never FAIL, since that's a valid state.
    assert all(c.status in ("PASS", "WARN") for c in codex_checks)

    # No FAIL should come from band I specifically. (Other bands may still
    # WARN/FAIL for reasons unrelated to codex, e.g. missing Claude Code
    # wiring in this sandboxed home — that's not what this test is checking.)
    codex_fail_forced = any(c.band == "I" and c.status == "FAIL" for c in checks)
    assert not codex_fail_forced
