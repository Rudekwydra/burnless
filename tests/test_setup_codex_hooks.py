import argparse
import json
import os
import stat
import tomllib
from pathlib import Path

from burnless.cli import _cmd_setup_codex


def _run_setup(home: Path, monkeypatch, *, dry_run: bool = False) -> int:
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    return _cmd_setup_codex(argparse.Namespace(codex=True, dry_run=dry_run))


def _installed_paths(home: Path) -> list[Path]:
    codex_dir = home / ".codex"
    return [
        codex_dir / "AGENTS.md",
        codex_dir / "hooks" / "burnless_epoch_session.sh",
        codex_dir / "hooks" / "burnless_epoch_stop.sh",
        codex_dir / "hooks" / "burnless_epoch_end.sh",
        codex_dir / "hooks" / "codex_payload.sh",
        codex_dir / "hooks.json",
    ]


def _burnless_commands(document: dict) -> list[str]:
    return [
        handler["command"]
        for groups in document["hooks"].values()
        for group in groups
        if isinstance(group, dict)
        for handler in group.get("hooks", [])
        if isinstance(handler, dict) and "burnless_epoch_" in handler.get("command", "")
    ]


def test_fresh_install_copies_executable_hooks_and_registers_real_events(
    tmp_path, monkeypatch, capsys
):
    assert _run_setup(tmp_path, monkeypatch) == 0

    paths = _installed_paths(tmp_path)
    assert all(path.is_file() for path in paths)
    for script in paths[1:5]:
        assert script.stat().st_mode & stat.S_IXUSR
        assert script.stat().st_mode & stat.S_IXGRP
        assert script.stat().st_mode & stat.S_IXOTH

    document = json.loads(paths[-1].read_text(encoding="utf-8"))
    assert set(("SessionStart", "Stop", "SessionEnd")) <= set(document["hooks"])
    commands = _burnless_commands(document)
    assert commands == [
        "bash ~/.codex/hooks/burnless_epoch_session.sh",
        "bash ~/.codex/hooks/burnless_epoch_stop.sh",
        "bash ~/.codex/hooks/burnless_epoch_end.sh",
    ]
    assert "/hooks" in capsys.readouterr().out


def test_second_install_is_a_filesystem_noop(tmp_path, monkeypatch, capsys):
    assert _run_setup(tmp_path, monkeypatch) == 0
    capsys.readouterr()

    paths = _installed_paths(tmp_path)
    fixed_ns = 1_700_000_000_000_000_000
    for path in paths:
        os.utime(path, ns=(fixed_ns, fixed_ns))
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

    assert _run_setup(tmp_path, monkeypatch) == 0
    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

    assert after == before
    assert "no changes" in capsys.readouterr().out


def test_dry_run_prints_diff_and_writes_nothing(tmp_path, monkeypatch, capsys):
    assert _run_setup(tmp_path, monkeypatch, dry_run=True) == 0

    assert not (tmp_path / ".codex").exists()
    output = capsys.readouterr().out
    assert "--- /dev/null" in output
    assert "+++ " in output
    assert "SessionEnd" in output


def test_existing_config_toml_is_preserved_byte_for_byte(tmp_path, monkeypatch):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True)
    config_path = codex_dir / "config.toml"
    original = b'''model = "gpt-5.6"\nnotify = ["python3", "/tmp/notify.py"]\n\n[projects."/tmp/project"]\ntrust_level = "trusted"\n\n[features]\nhooks = true\n'''
    config_path.write_bytes(original)

    assert _run_setup(tmp_path, monkeypatch) == 0

    assert config_path.read_bytes() == original
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["model"] == "gpt-5.6"
    assert parsed["notify"] == ["python3", "/tmp/notify.py"]
    assert parsed["projects"]["/tmp/project"]["trust_level"] == "trusted"
    assert parsed["features"]["hooks"] is True
    assert not (codex_dir / "config.toml.bak-burnless").exists()


def test_existing_hooks_are_preserved_and_old_burnless_entries_are_normalized(
    tmp_path, monkeypatch
):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir(parents=True)
    hooks_path = codex_dir / "hooks.json"
    existing = {
        "description": "user hooks",
        "custom": {"keep": True},
        "hooks": {
            "Stop": [
                {"hooks": [{"type": "command", "command": "echo user"}]},
                {
                    "hooks": [
                        {"type": "command", "command": "./burnless_epoch_stop.sh"}
                    ]
                },
            ],
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "echo reviewed"}],
                }
            ],
        },
    }
    hooks_path.write_text(json.dumps(existing), encoding="utf-8")

    assert _run_setup(tmp_path, monkeypatch) == 0

    document = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert document["description"] == "user hooks"
    assert document["custom"] == {"keep": True}
    assert document["hooks"]["PostToolUse"] == existing["hooks"]["PostToolUse"]
    assert any(
        handler.get("command") == "echo user"
        for group in document["hooks"]["Stop"]
        for handler in group.get("hooks", [])
    )
    assert len(_burnless_commands(document)) == 3
    assert (codex_dir / "hooks.json.bak-burnless").is_file()
