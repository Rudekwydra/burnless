"""P0 runtime tests: atomic ID allocation, run snapshot, BURNLESS_WORKER, short output, progress modes."""
from __future__ import annotations

import io
import json
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from burnless import agents, metrics, paths, state


# ── 1. Atomic delegation ID allocation ──────────────────────────────────────


def test_alloc_delegation_id_is_unique_under_concurrency(tmp_path: Path):
    state_path = tmp_path / ".burnless" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state.save(state_path, dict(state.DEFAULT_STATE))

    ids: list[str] = []
    errors: list[Exception] = []

    def alloc():
        try:
            ids.append(state.alloc_delegation_id(state_path))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=alloc) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Allocation errors: {errors}"
    assert len(ids) == len(set(ids)), f"Duplicate IDs generated: {sorted(ids)}"
    assert len(ids) == 20


def test_alloc_delegation_id_persists_counter(tmp_path: Path):
    state_path = tmp_path / ".burnless" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state.save(state_path, dict(state.DEFAULT_STATE))

    did = state.alloc_delegation_id(state_path)
    assert did == "d001"

    reloaded = state.load(state_path)
    assert reloaded["delegation_counter"] == 1

    did2 = state.alloc_delegation_id(state_path)
    assert did2 == "d002"


# ── 2. Run snapshot ──────────────────────────────────────────────────────────


def _make_project(tmp_path: Path):
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
        p[key].mkdir(parents=True, exist_ok=True)
    p["config"].write_text(
        """
project_name: test
agents:
  gold:
    name: opus
    command: "printf ok"
    role: strategy
  silver:
    name: sonnet
    command: "printf ok"
    role: execution
  bronze:
    name: haiku
    command: "printf ok"
    role: cheap
routing:
  gold: []
  silver: []
  bronze: []
metrics:
  expensive_model_usd_per_million: 15.0
compression:
  mode: balanced
""",
        encoding="utf-8",
    )
    state.save(p["state"], state.DEFAULT_STATE | {"project": "test"})
    metrics.save(p["metrics"], metrics._fresh())
    p["history"].write_text("# history\n", encoding="utf-8")
    return root, p


def _make_fake_runner(p, did, *, summary_json: str | None = None):
    """Return a fake run_with_live_panel that writes the log and returns a RunResult."""
    from burnless.live_runner import RunResult
    from datetime import datetime, timezone

    if summary_json is None:
        summary_json = (
            '{"id":"' + did + '","status":"OK","summary":"done",'
            '"files_touched":[],"validated":[],"evidence":["printf ok"],"issues":[],"next":""}'
        )

    def fake_run_with_live_panel(**kwargs):
        log_path = kwargs.get("log_path")
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("# fake log\n", encoding="utf-8")
        now = datetime.now(timezone.utc).isoformat()
        return RunResult(
            agent="haiku",
            command=["printf", "ok"],
            stdout=f"```json\n{summary_json}\n```",
            stderr="",
            returncode=0,
            started_at=now,
            ended_at=now,
            duration_s=0.1,
        )

    return fake_run_with_live_panel


def test_cmd_run_creates_plan_snapshot(tmp_path: Path, monkeypatch):
    import argparse
    from burnless import cli as cli_mod

    root, p = _make_project(tmp_path)
    monkeypatch.setattr(cli_mod.paths_mod, "require_root", lambda: root)

    did = state.alloc_delegation_id(p["state"])
    deleg_content = (
        "# Delegation d001\n- **agent:** haiku\n- **tier:** bronze\n\n## Goal\ntest\n\n## Task\ntest\n"
    )
    (p["delegations"] / f"{did}.md").write_text(deleg_content, encoding="utf-8")

    monkeypatch.setattr(cli_mod.live_runner, "run_with_live_panel", _make_fake_runner(p, did))

    args = argparse.Namespace(
        id=did, dry_run=False, timeout=30, mode="plain", maestro=False, no_maestro=False
    )
    cli_mod.cmd_run(args)

    plan_path = p["runs"] / f"{did}.plan.json"
    assert plan_path.exists(), f"Expected {plan_path} to exist"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["id"] == did
    assert plan["tier"] == "bronze"
    assert plan["agent"] == "haiku"
    assert "started_at" in plan
    assert plan["delegation"].endswith(f"{did}.md")


# ── 3. BURNLESS_WORKER env var ───────────────────────────────────────────────


def test_agents_run_sets_burnless_worker(monkeypatch):
    captured: list[dict] = []

    class FakeResult:
        stdout = ""
        stderr = ""
        returncode = 0

    def fake_subprocess_run(*args, **kwargs):
        captured.append(kwargs)
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    agent_cfg = {"name": "test", "command": "printf ok"}
    agents.run(agent_cfg, "hello", timeout=5)

    assert captured, "subprocess.run was not called"
    env = captured[0].get("env", {})
    assert env.get("BURNLESS_WORKER") == "1", f"BURNLESS_WORKER not set in env: {env}"


def test_live_runner_sets_burnless_worker(monkeypatch, tmp_path: Path):
    import queue
    from burnless import live_runner

    captured_env: dict = {}

    class FakeProc:
        stdin = None
        stdout = None
        stderr = None
        returncode = 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    class FakeStream:
        def __iter__(self):
            return iter([])

        def read(self, n=-1):
            return ""

        def close(self):
            pass

    fake_proc = FakeProc()
    fake_proc.stdout = FakeStream()
    fake_proc.stderr = FakeStream()

    import io
    stdin_buf = io.StringIO()
    fake_proc.stdin = stdin_buf

    def fake_popen(cmd, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        return fake_proc

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    log_path = tmp_path / "test.log"
    agent_cfg = {"name": "test", "command": "printf ok"}

    try:
        live_runner.run_with_live_panel(
            delegation_id="d001",
            tier="bronze",
            agent_cfg=agent_cfg,
            prompt="hello",
            log_path=log_path,
            mode="plain",
            timeout=5,
        )
    except Exception:
        pass  # We just need to verify the env was set before the process ran

    assert captured_env.get("BURNLESS_WORKER") == "1", (
        f"BURNLESS_WORKER not in Popen env: {captured_env}"
    )


# ── 4. Short output from cmd_run ─────────────────────────────────────────────


def test_cmd_run_output_is_short(tmp_path: Path, monkeypatch, capsys):
    import argparse
    from burnless import cli as cli_mod

    root, p = _make_project(tmp_path)
    monkeypatch.setattr(cli_mod.paths_mod, "require_root", lambda: root)

    did = state.alloc_delegation_id(p["state"])
    deleg_content = (
        "# Delegation d001\n- **agent:** haiku\n- **tier:** bronze\n\n## Goal\ntest\n\n## Task\ntest\n"
    )
    (p["delegations"] / f"{did}.md").write_text(deleg_content, encoding="utf-8")

    # No evidence → audit not triggered → status stays OK (cleaner output check)
    summary_json = (
        '{"id":"d001","status":"OK","summary":"All done fine.",'
        '"files_touched":[],"validated":[],"evidence":[],'
        '"issues":[],"next":"deploy"}'
    )
    monkeypatch.setattr(
        cli_mod.live_runner, "run_with_live_panel",
        _make_fake_runner(p, did, summary_json=summary_json),
    )

    args = argparse.Namespace(
        id=did, dry_run=False, timeout=30, mode="plain", maestro=False, no_maestro=False
    )
    cli_mod.cmd_run(args)

    out = capsys.readouterr().out

    # Silent-default prints the bounded one-line DoneReport with summary (Phase 6A)
    assert "OK:d001" in out
    assert "All done fine." in out   # summary included in silent-default one-line (Phase 6A)
    # Verbose fields must NOT appear
    assert "Audit:" not in out
    assert "Evidence:" not in out
    assert "Capsule" not in out
    assert "burnless tokens" not in out


# ── 5. Progress modes ────────────────────────────────────────────────────────


def test_detect_phase_reading():
    from burnless.live_runner import _detect_phase
    assert _detect_phase("Reading src/foo.py") == "lendo"


def test_detect_phase_editing():
    from burnless.live_runner import _detect_phase
    assert _detect_phase("Writing src/bar.py") == "editando"
    assert _detect_phase("Updated src/baz.py") == "editando"
    assert _detect_phase("Applying patch") == "editando"


def test_detect_phase_testing():
    from burnless.live_runner import _detect_phase
    assert _detect_phase("Running tests") == "testando"
    assert _detect_phase("Tests passed") == "testando"
    assert _detect_phase("Command failed") == "testando"


def test_detect_phase_compacting():
    from burnless.live_runner import _detect_phase
    assert _detect_phase("Waiting for final JSON...") == "compactando"
    assert _detect_phase("capsule ready") == "compactando"


def test_detect_phase_none_returns_pensando():
    from burnless.live_runner import _detect_phase
    assert _detect_phase(None) == "pensando"
    assert _detect_phase("") == "pensando"
    assert _detect_phase("some random unrelated text") == "pensando"


def test_config_progress_detail_default():
    from burnless import config
    cfg = config.DEFAULT_CONFIG
    assert cfg.get("display", {}).get("progress_detail") == "brief"


def test_config_progress_detail_loaded_with_default(tmp_path: Path):
    from burnless import config
    cfg = config.load(tmp_path / "nonexistent.yaml")
    assert cfg.get("display", {}).get("progress_detail") == "brief"


def test_minimal_spinner_nontty_prints_static(tmp_path: Path, capsys):
    from burnless.live_runner import _MinimalSpinner
    spinner = _MinimalSpinner(delegation_id="d001", tier="bronze")
    # non-tty start() prints nothing under silent-default
    spinner.start()
    out = capsys.readouterr().out
    assert out == ""   # non-tty spinner is silent under silent-default


def test_minimal_spinner_stop_nontty_is_noop(capsys):
    from burnless.live_runner import _MinimalSpinner
    spinner = _MinimalSpinner(delegation_id="d001", tier="bronze")
    spinner.stop()  # should not raise


def test_minimal_spinner_start_tty_renders_without_type_error():
    import io
    import sys
    from unittest.mock import patch
    from burnless.live_runner import _MinimalSpinner

    spinner = _MinimalSpinner(delegation_id="d001", tier="bronze")
    spinner._enabled = True

    buf = io.StringIO()
    with patch.object(sys, "stdout", buf):
        assert spinner.start() is True

    assert "d001" in buf.getvalue()


def test_cmd_run_progress_flag_minimal_passed_to_runner(tmp_path: Path, monkeypatch):
    """--progress minimal must reach run_with_live_panel as mode='minimal'."""
    import argparse
    from burnless import cli as cli_mod

    root, p = _make_project(tmp_path)
    monkeypatch.setattr(cli_mod.paths_mod, "require_root", lambda: root)

    did = state.alloc_delegation_id(p["state"])
    (p["delegations"] / f"{did}.md").write_text(
        "# Delegation d001\n- **agent:** haiku\n- **tier:** bronze\n\n## Goal\ntest\n\n## Task\ntest\n",
        encoding="utf-8",
    )

    captured_modes: list[str] = []

    def capturing_runner(**kwargs):
        captured_modes.append(kwargs.get("mode", ""))
        return _make_fake_runner(p, did)(**kwargs)

    monkeypatch.setattr(cli_mod.live_runner, "run_with_live_panel", capturing_runner)

    args = argparse.Namespace(
        id=did, dry_run=False, timeout=30,
        mode="plain", progress="minimal",
        maestro=False, no_maestro=False,
    )
    cli_mod.cmd_run(args)

    assert captured_modes == ["minimal"], f"Expected ['minimal'], got {captured_modes}"


def test_cmd_run_progress_flag_full_passed_to_runner(tmp_path: Path, monkeypatch):
    """--progress full must reach run_with_live_panel as mode='full'."""
    import argparse
    from burnless import cli as cli_mod

    root, p = _make_project(tmp_path)
    monkeypatch.setattr(cli_mod.paths_mod, "require_root", lambda: root)

    did = state.alloc_delegation_id(p["state"])
    (p["delegations"] / f"{did}.md").write_text(
        "# Delegation d001\n- **agent:** haiku\n- **tier:** bronze\n\n## Goal\ntest\n\n## Task\ntest\n",
        encoding="utf-8",
    )

    captured_modes: list[str] = []

    def capturing_runner(**kwargs):
        captured_modes.append(kwargs.get("mode", ""))
        return _make_fake_runner(p, did)(**kwargs)

    monkeypatch.setattr(cli_mod.live_runner, "run_with_live_panel", capturing_runner)

    args = argparse.Namespace(
        id=did, dry_run=False, timeout=30,
        mode="plain", progress="full",
        maestro=False, no_maestro=False,
    )
    cli_mod.cmd_run(args)

    assert captured_modes == ["full"]


def test_cmd_run_config_progress_detail_used_when_no_flag(tmp_path: Path, monkeypatch):
    """When --progress is absent, display.progress_detail from config is used."""
    import argparse
    from burnless import cli as cli_mod

    root, p = _make_project(tmp_path)
    # Override config to set progress_detail=minimal
    p["config"].write_text(
        p["config"].read_text(encoding="utf-8") + "\ndisplay:\n  progress_detail: minimal\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_mod.paths_mod, "require_root", lambda: root)

    did = state.alloc_delegation_id(p["state"])
    (p["delegations"] / f"{did}.md").write_text(
        "# Delegation d001\n- **agent:** haiku\n- **tier:** bronze\n\n## Goal\ntest\n\n## Task\ntest\n",
        encoding="utf-8",
    )

    captured_modes: list[str] = []

    def capturing_runner(**kwargs):
        captured_modes.append(kwargs.get("mode", ""))
        return _make_fake_runner(p, did)(**kwargs)

    monkeypatch.setattr(cli_mod.live_runner, "run_with_live_panel", capturing_runner)

    args = argparse.Namespace(
        id=did, dry_run=False, timeout=30,
        mode="plain", progress=None,
        maestro=False, no_maestro=False,
    )
    cli_mod.cmd_run(args)

    assert captured_modes == ["minimal"]


def test_minimal_spinner_idle_appears_in_render(capsys):
    """When refresh is called with idle_s >= 2, idle label must appear in spinner output."""
    import io
    import sys
    from unittest.mock import patch
    from burnless.live_runner import _MinimalSpinner

    spinner = _MinimalSpinner(delegation_id="d007", tier="silver")
    spinner._enabled = True

    buf = io.StringIO()
    with patch.object(sys, "stdout", buf):
        spinner.refresh(elapsed_s=15.0, idle_s=7.0)

    out = buf.getvalue()
    assert "idle 7s" in out, f"Expected 'idle 7s' in spinner output, got: {out!r}"
    assert "d007" in out


def test_minimal_spinner_no_idle_below_threshold(capsys):
    """When idle_s < 2, no idle label should appear."""
    import io
    import sys
    from unittest.mock import patch
    from burnless.live_runner import _MinimalSpinner

    spinner = _MinimalSpinner(delegation_id="d007", tier="silver")
    spinner._enabled = True

    buf = io.StringIO()
    with patch.object(sys, "stdout", buf):
        spinner.refresh(elapsed_s=5.0, idle_s=1.0)

    out = buf.getvalue()
    assert "idle" not in out, f"Expected no idle label for idle_s=1.0, got: {out!r}"


def test_watch_renderer_rich_renderable_shows_idle():
    """_rich_renderable must include idle text when idle_s >= 2."""
    import io
    from burnless.live_runner import _WatchRenderer

    renderer = _WatchRenderer(
        enabled=True,
        delegation_id="d007",
        tier="silver",
        agent="sonnet",
        log_path=Path("/tmp/fake.log"),
        burnless_tokens=0,
        tail_lines=5,
    )
    try:
        from rich.console import Console
        console = Console(file=io.StringIO(), width=120)
        renderable = renderer._rich_renderable(30.0, ["Reading file.py"], "running", idle_s=9.0)
        buf = io.StringIO()
        console.print(renderable, end="")
        output = console.file.getvalue()
        assert "heartbeat:" in output, f"Expected heartbeat label in rich renderable, got: {output!r}"
        assert "idle 9s" in output, f"Expected 'idle 9s' in rich renderable, got: {output!r}"
    except ImportError:
        pytest.skip("rich not installed")


def test_format_idle():
    from burnless.live_runner import _format_idle
    assert _format_idle(0) == "0s"
    assert _format_idle(7) == "7s"
    assert _format_idle(59) == "59s"
    assert _format_idle(60) == "1m 0s"
    assert _format_idle(83) == "1m 23s"


def test_cmd_run_progress_flag_overrides_config(tmp_path: Path, monkeypatch):
    """--progress brief overrides display.progress_detail=full in config."""
    import argparse
    from burnless import cli as cli_mod

    root, p = _make_project(tmp_path)
    p["config"].write_text(
        p["config"].read_text(encoding="utf-8") + "\ndisplay:\n  progress_detail: full\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_mod.paths_mod, "require_root", lambda: root)

    did = state.alloc_delegation_id(p["state"])
    (p["delegations"] / f"{did}.md").write_text(
        "# Delegation d001\n- **agent:** haiku\n- **tier:** bronze\n\n## Goal\ntest\n\n## Task\ntest\n",
        encoding="utf-8",
    )

    captured_modes: list[str] = []

    def capturing_runner(**kwargs):
        captured_modes.append(kwargs.get("mode", ""))
        return _make_fake_runner(p, did)(**kwargs)

    monkeypatch.setattr(cli_mod.live_runner, "run_with_live_panel", capturing_runner)

    args = argparse.Namespace(
        id=did, dry_run=False, timeout=30,
        mode="plain", progress="brief",
        maestro=False, no_maestro=False,
    )
    cli_mod.cmd_run(args)

    assert captured_modes == ["brief"]
