"""Unit tests for stale/heartbeat detection in live_runner (no real Claude)."""
from __future__ import annotations

import io
import queue
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from burnless import config, live_runner
from burnless.live_runner import RunResult, _stop_process


# ── 1. Config ────────────────────────────────────────────────────────────────


def test_stale_timeout_seconds_in_default_config():
    cfg = config.DEFAULT_CONFIG
    assert "stale_timeout_seconds" in cfg.get("display", {}), (
        "display.stale_timeout_seconds missing from DEFAULT_CONFIG"
    )
    assert cfg["display"]["stale_timeout_seconds"] == 300


def test_stale_timeout_seconds_loaded_from_config(tmp_path: Path):
    cfg = config.load(tmp_path / "nonexistent.yaml")
    assert cfg["display"]["stale_timeout_seconds"] == 300


def test_stale_timeout_seconds_overridable(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("display:\n  stale_timeout_seconds: 60\n", encoding="utf-8")
    cfg = config.load(cfg_path)
    assert cfg["display"]["stale_timeout_seconds"] == 60


# ── 2. RunResult.stale field ─────────────────────────────────────────────────


def test_run_result_has_stale_field():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    result = RunResult(
        agent="test", command=["printf", "ok"],
        stdout="", stderr="", returncode=0,
        started_at=now, ended_at=now, duration_s=0.1,
    )
    assert result.stale is False


def test_run_result_stale_true_in_to_dict():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    result = RunResult(
        agent="test", command=["printf", "ok"],
        stdout="", stderr="", returncode=0,
        started_at=now, ended_at=now, duration_s=0.1,
        stale=True,
    )
    d = result.to_dict()
    assert d["stale"] is True


def test_run_result_stale_false_in_to_dict_by_default():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    result = RunResult(
        agent="test", command=["printf", "ok"],
        stdout="", stderr="", returncode=0,
        started_at=now, ended_at=now, duration_s=0.1,
    )
    d = result.to_dict()
    assert d["stale"] is False


# ── 3. Stale detection in run_with_live_panel ─────────────────────────────────


class _SilentProc:
    """Fake process that produces no output, hangs until killed."""

    def __init__(self):
        self.returncode = None
        self.stdin = io.StringIO()
        self._killed = threading.Event()
        self.stdout = _BlockingStream(self._killed)
        self.stderr = _BlockingStream(self._killed)

    def poll(self):
        if self._killed.is_set():
            self.returncode = 130
        return self.returncode

    def terminate(self):
        self._killed.set()
        self.returncode = 130

    def kill(self):
        self._killed.set()
        self.returncode = 130

    def wait(self, timeout=None):
        self._killed.wait(timeout=timeout or 5)
        return self.returncode or 130


class _BlockingStream:
    """Stream that yields nothing and blocks until killed."""

    def __init__(self, killed: threading.Event):
        self._killed = killed

    def __iter__(self):
        self._killed.wait()
        return iter([])

    def close(self):
        pass


def _make_silent_popen(proc: _SilentProc):
    def fake_popen(cmd, **kwargs):
        return proc
    return fake_popen


def test_stale_detection_kills_silent_worker(monkeypatch, tmp_path: Path):
    """A worker that emits no output gets killed after stale_timeout seconds."""
    silent_proc = _SilentProc()
    monkeypatch.setattr(subprocess, "Popen", _make_silent_popen(silent_proc))

    log_path = tmp_path / "test.log"
    agent_cfg = {"name": "test", "command": "printf ok"}

    result = live_runner.run_with_live_panel(
        delegation_id="d001",
        tier="bronze",
        agent_cfg=agent_cfg,
        prompt="hello",
        log_path=log_path,
        mode="plain",
        timeout=60,
        stale_timeout=1,  # 1 second for fast test
    )

    assert result.stale is True
    assert result.interrupted is True
    assert silent_proc._killed.is_set(), "Process was not killed"


def test_stale_detection_not_triggered_before_timeout(monkeypatch, tmp_path: Path):
    """A worker that emits output regularly is not killed by stale detection."""

    class _TalkativeProc:
        returncode = None
        stdin = io.StringIO()

        def __init__(self):
            self._lines_sent = 0
            self._done = threading.Event()
            self.stdout = self
            self.stderr = _EmptyStream()

        def __iter__(self):
            for i in range(5):
                time.sleep(0.05)
                yield f"line {i}\n"
            self._done.set()

        def close(self):
            pass

        def poll(self):
            if self._done.is_set():
                self.returncode = 0
            return self.returncode

        def terminate(self):
            self.returncode = 130

        def kill(self):
            self.returncode = 130

        def wait(self, timeout=None):
            return self.returncode or 0

    class _EmptyStream:
        def __iter__(self):
            return iter([])
        def close(self):
            pass

    talkative = _TalkativeProc()
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: talkative)

    log_path = tmp_path / "test.log"
    agent_cfg = {"name": "test", "command": "printf ok"}

    result = live_runner.run_with_live_panel(
        delegation_id="d001",
        tier="bronze",
        agent_cfg=agent_cfg,
        prompt="hello",
        log_path=log_path,
        mode="plain",
        timeout=60,
        stale_timeout=5,  # 5 seconds; worker finishes in ~0.25s
    )

    assert result.stale is False


def test_stale_timeout_zero_disables_stale_detection(monkeypatch, tmp_path: Path):
    """stale_timeout=0 must not trigger stale detection even for a silent worker."""

    class _QuickStream:
        def __iter__(self):
            return iter([])
        def close(self):
            pass

    class _QuickProc:
        returncode = 0
        stdin = io.StringIO()

        def __init__(self):
            self.stdout = _QuickStream()
            self.stderr = _QuickStream()

        def poll(self):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _QuickProc())

    log_path = tmp_path / "test.log"
    agent_cfg = {"name": "test", "command": "printf ok"}

    result = live_runner.run_with_live_panel(
        delegation_id="d001",
        tier="bronze",
        agent_cfg=agent_cfg,
        prompt="hello",
        log_path=log_path,
        mode="plain",
        timeout=60,
        stale_timeout=0,  # disabled
    )

    assert result.stale is False


# ── 4. Stale produces PART + stale_worker issue in cmd_run ───────────────────


def _make_project(tmp_path: Path):
    from burnless import metrics, paths, state
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
        p[key].mkdir(parents=True, exist_ok=True)
    p["config"].write_text(
        "project_name: test\n"
        "agents:\n"
        "  gold:\n    name: opus\n    command: 'printf ok'\n    role: strategy\n"
        "  silver:\n    name: sonnet\n    command: 'printf ok'\n    role: execution\n"
        "  bronze:\n    name: haiku\n    command: 'printf ok'\n    role: cheap\n"
        "routing:\n  gold: []\n  silver: []\n  bronze: []\n"
        "metrics:\n  expensive_model_usd_per_million: 15.0\n"
        "compression:\n  mode: balanced\n"
        "display:\n  stale_timeout_seconds: 300\n",
        encoding="utf-8",
    )
    state.save(p["state"], state.DEFAULT_STATE | {"project": "test"})
    metrics.save(p["metrics"], metrics._fresh())
    p["history"].write_text("# history\n", encoding="utf-8")
    return root, p


def test_stale_result_produces_part_with_stale_worker_issue(tmp_path: Path, monkeypatch, capsys):
    import argparse
    from burnless import cli as cli_mod, paths as paths_mod, state

    root, p = _make_project(tmp_path)
    monkeypatch.setattr(cli_mod.paths_mod, "require_root", lambda: root)

    did = state.alloc_delegation_id(p["state"])
    (p["delegations"] / f"{did}.md").write_text(
        f"# Delegation {did}\n- **agent:** haiku\n- **tier:** bronze\n\n## Goal\ntest\n\n## Task\ntest\n",
        encoding="utf-8",
    )

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    def fake_stale_runner(**kwargs):
        # Simulate a stale kill: no JSON output, interrupted, stale=True
        log_path = kwargs.get("log_path")
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("# fake stale log\n", encoding="utf-8")
        return RunResult(
            agent="haiku",
            command=["printf", "ok"],
            stdout="",
            stderr="",
            returncode=130,
            started_at=now,
            ended_at=now,
            duration_s=0.1,
            interrupted=True,
            stale=True,
        )

    monkeypatch.setattr(cli_mod.live_runner, "run_with_live_panel", fake_stale_runner)

    args = argparse.Namespace(
        id=did, dry_run=False, timeout=30, mode="plain", maestro=False, no_maestro=False
    )
    exit_code = cli_mod.cmd_run(args)

    # stale → PART → exit_code 1
    assert exit_code == 1

    out = capsys.readouterr().out
    assert did in out
    assert "stale" in out.lower() or "PART" in out, f"Expected stale/PART in output: {out!r}"

    # Verify the persisted summary has stale_worker issue
    summary_path = p["temp"] / f"{did}.json"
    import json
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "PART", f"Expected PART, got {summary['status']}"
    assert "stale_worker" in summary.get("issues", []), f"Expected stale_worker issue: {summary}"


def test_stale_output_is_short(tmp_path: Path, monkeypatch, capsys):
    """Stale output must be short — no verbose fields."""
    import argparse
    from burnless import cli as cli_mod, state

    root, p = _make_project(tmp_path)
    monkeypatch.setattr(cli_mod.paths_mod, "require_root", lambda: root)

    did = state.alloc_delegation_id(p["state"])
    (p["delegations"] / f"{did}.md").write_text(
        f"# Delegation {did}\n- **agent:** haiku\n- **tier:** bronze\n\n## Goal\ntest\n\n## Task\ntest\n",
        encoding="utf-8",
    )

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    def fake_stale_runner(**kwargs):
        log_path = kwargs.get("log_path")
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("# fake stale log\n", encoding="utf-8")
        return RunResult(
            agent="haiku", command=["printf", "ok"],
            stdout="", stderr="", returncode=130,
            started_at=now, ended_at=now, duration_s=0.1,
            interrupted=True, stale=True,
        )

    monkeypatch.setattr(cli_mod.live_runner, "run_with_live_panel", fake_stale_runner)

    args = argparse.Namespace(
        id=did, dry_run=False, timeout=30, mode="plain", maestro=False, no_maestro=False
    )
    cli_mod.cmd_run(args)

    out = capsys.readouterr().out
    # Must NOT contain verbose fields
    assert "burnless tokens" not in out
    assert "Evidence:" not in out
    assert "Capsule" not in out
    # Must NOT print the old "Worker stopped by user." message
    assert "Worker stopped by user." not in out
