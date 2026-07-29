import argparse
import json
import os
import stat
import subprocess
from pathlib import Path

from burnless.cli import _cmd_setup_codex

import pytest

tomllib = pytest.importorskip("tomllib")  # stdlib so no 3.11+; CI ainda roda 3.10


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


def _install_recording_burnless(home: Path) -> Path:
    executable = home / ".local" / "bin" / "burnless"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        """#!/bin/bash
printf '%s\\n' "$*" >>"$BURNLESS_TEST_ARG_LOG"
case "$1 $2" in
  "epoch extract-exchange")
    printf '%s\\n' '{"question":"q","answer":"a"}'
    ;;
  "epoch resolve-root")
    printf '%s\\n' "$BURNLESS_TEST_ROOT"
    ;;
  "epoch journal-append")
    cat
    ;;
esac
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _run_installed_stop(
    home: Path,
    payload: dict,
    project_root: Path,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    log_path = home / "burnless-args.log"
    env = {
        **os.environ,
        "HOME": str(home),
        "BURNLESS_TEST_ARG_LOG": str(log_path),
        "BURNLESS_TEST_ROOT": str(project_root),
    }
    result = subprocess.run(
        ["bash", str(home / ".codex" / "hooks" / "burnless_epoch_stop.sh")],
        input=json.dumps(payload),
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    calls = log_path.read_text(encoding="utf-8").splitlines()
    return result, calls


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
        f"bash {tmp_path / '.codex' / 'hooks' / 'burnless_epoch_session.sh'}",
        f"bash {tmp_path / '.codex' / 'hooks' / 'burnless_epoch_stop.sh'}",
        f"bash {tmp_path / '.codex' / 'hooks' / 'burnless_epoch_end.sh'}",
    ]
    assert all(command.startswith(f"bash {tmp_path.resolve()}") for command in commands)
    assert "/hooks" in capsys.readouterr().out


def test_payload_transcript_path_reaches_every_epoch_call(tmp_path, monkeypatch):
    assert _run_setup(tmp_path, monkeypatch) == 0
    _install_recording_burnless(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    transcript = tmp_path / "custom-codex-home" / "sessions" / "rollout.jsonl"

    result, calls = _run_installed_stop(
        tmp_path,
        {
            "session_id": "019f9118-0335-71c2-8818-2e3387897288",
            "cwd": str(project_root),
            "transcript_path": str(transcript),
        },
        project_root,
    )

    assert result.returncode == 0, result.stderr
    epoch_calls = [call for call in calls if call.startswith("epoch ")]
    assert len([call for call in epoch_calls if call.startswith("epoch extract-exchange")]) == 2
    assert epoch_calls
    assert all(f"--transcript {transcript}" in call for call in epoch_calls)


def test_payload_without_transcript_preserves_derived_path_fallback(tmp_path, monkeypatch):
    assert _run_setup(tmp_path, monkeypatch) == 0
    _install_recording_burnless(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()

    result, calls = _run_installed_stop(
        tmp_path,
        {
            "session_id": "019f9118-0335-71c2-8818-2e3387897288",
            "cwd": str(project_root),
        },
        project_root,
    )

    assert result.returncode == 0, result.stderr
    epoch_calls = [call for call in calls if call.startswith("epoch ")]
    assert len([call for call in epoch_calls if call.startswith("epoch extract-exchange")]) == 2
    assert epoch_calls
    assert all("--transcript" not in call for call in epoch_calls)


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
