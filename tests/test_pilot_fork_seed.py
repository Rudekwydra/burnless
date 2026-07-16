"""Route D: fork-seed mode for the pilot claude host.

When pilot.fork.enabled is on, the post-rollover restore is injected exactly
once via --append-system-prompt (channel C) instead of the native
SessionStart `epoch restore` hook. Fork off (default) stays byte-identical
to today's behavior.
"""
from __future__ import annotations

import argparse
import json

import pytest

from burnless import cli, recovery
from burnless.pilot.core import build_child_env


@pytest.fixture(autouse=True)
def _isolate_global_config(monkeypatch):
    monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", "")
    monkeypatch.delenv("BURNLESS_PROFILE", raising=False)
    monkeypatch.delenv("BURNLESS_PILOT_FORK", raising=False)


def _seed(root):
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo denso\n\n## Threads abertas\n- thread viva\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    recovery.journal_append(
        root,
        {
            "schema": 1,
            "host": "claude",
            "host_session_id": "sid-1",
            "process_instance_id": "proc-1",
            "transcript_path": "/tmp/t.jsonl",
            "exchange_id": "sha256:fork-1",
            "user_text": "pergunta",
            "assistant_text": "resposta",
            "files": [],
        },
    )


def _restore_args(root, source="clear"):
    return argparse.Namespace(
        epoch_cmd="restore",
        root=str(root),
        host="claude",
        host_session_id="sid-1",
        session_id=None,
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source=source,
        budget_tokens=None,
    )


# --- _pilot_fork_enabled -----------------------------------------------


def test_pilot_fork_enabled_default_false(tmp_path):
    project_root = tmp_path
    (project_root / ".burnless").mkdir(parents=True)
    (project_root / ".burnless" / "config.yaml").write_text("pilot:\n  host: claude\n", encoding="utf-8")
    assert cli._pilot_fork_enabled(project_root) is False


def test_pilot_fork_enabled_true_from_config(tmp_path):
    project_root = tmp_path
    (project_root / ".burnless").mkdir(parents=True)
    (project_root / ".burnless" / "config.yaml").write_text(
        "pilot:\n  fork:\n    enabled: true\n", encoding="utf-8"
    )
    assert cli._pilot_fork_enabled(project_root) is True


def test_pilot_fork_enabled_missing_config_is_false(tmp_path):
    project_root = tmp_path / "no-such-project"
    assert cli._pilot_fork_enabled(project_root) is False


# --- build_child_env fork param -----------------------------------------


def test_build_child_env_fork_off_by_default(monkeypatch):
    monkeypatch.setenv("TEST_VAR", "x")
    env = build_child_env("r1")
    assert "BURNLESS_PILOT_FORK" not in env


def test_build_child_env_fork_on_sets_flag(monkeypatch):
    monkeypatch.setenv("TEST_VAR", "x")
    env = build_child_env("r1", fork=True)
    assert env["BURNLESS_PILOT_FORK"] == "1"


# --- epoch restore native suppression under fork ------------------------


def test_restore_clear_silent_when_fork_flag_set(tmp_path, monkeypatch, capsys):
    root = tmp_path / ".burnless"
    _seed(root)
    monkeypatch.setenv("BURNLESS_PILOT_FORK", "1")

    rc = cli.cmd_epoch(_restore_args(root, source="clear"))
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_restore_clear_native_works_when_fork_flag_absent(tmp_path, capsys):
    root = tmp_path / ".burnless"
    _seed(root)

    rc = cli.cmd_epoch(_restore_args(root, source="clear"))
    assert rc == 0
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["additionalContext"]


def test_restore_startup_not_suppressed_by_fork_flag(tmp_path, monkeypatch, capsys):
    root = tmp_path / ".burnless"
    _seed(root)
    monkeypatch.setenv("BURNLESS_PILOT_FORK", "1")

    rc = cli.cmd_epoch(_restore_args(root, source="startup"))
    assert rc == 0
    out = capsys.readouterr().out.strip()
    # source=startup is untouched by the fork gate (only source=="clear" is silenced)
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["additionalContext"]
