from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import yaml


def _make_script(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_run_pilot_capture_roundtrip(tmp_path):
    from burnless.pilot import run_pilot

    script = Path(__file__).resolve().parent / "fixtures" / "fake_tui_host.py"

    rc, out = run_pilot([sys.executable, str(script)], capture=True, input_bytes=b"ping\n")
    assert rc == 0
    assert "\x1b[?1049h" in out
    assert "fake-host" in out
    assert "ping" in out


def test_run_pilot_relay_roundtrip(monkeypatch):
    from burnless.pilot import run_pilot

    script = Path(__file__).resolve().parent / "fixtures" / "fake_tui_host.py"
    spawned = {}

    class _FD:
        def __init__(self, fd):
            self._fd = fd

        def fileno(self):
            return self._fd

    stdin_fd = os.open(os.devnull, os.O_RDONLY)
    stdout_r, stdout_w = os.pipe()
    monkeypatch.setattr(sys, "stdin", _FD(stdin_fd))
    monkeypatch.setattr(sys, "stdout", _FD(stdout_w))

    def on_spawn(proc):
        spawned["pid"] = proc.pid

    try:
        rc = run_pilot([sys.executable, str(script)], input_bytes=b"ping\n/clear\n/exit\n", on_spawn=on_spawn)
    finally:
        os.close(stdin_fd)
        os.close(stdout_w)

    out = b""
    while True:
        chunk = os.read(stdout_r, 4096)
        if not chunk:
            break
        out += chunk
    os.close(stdout_r)
    out_text = out.decode("utf-8", "replace")
    assert rc == 0
    assert spawned["pid"] > 0
    assert "\x1b[?1049h" in out_text
    assert "fake-host ready" in out_text
    assert "echo:ping" in out_text
    assert "reset" in out_text
    assert "\x1b[?1049l" in out_text


def test_claude_adapter_builds_argv(tmp_path):
    from burnless.pilot.hosts.claude import ClaudeAdapter

    adapter = ClaudeAdapter()
    argv = adapter.build_interactive_argv(tmp_path, model="sonnet", extra_args=["--foo", "bar"])
    assert argv[0] == "claude"
    assert "-C" not in argv  # claude has no -C cwd flag; pilot sets cwd on spawn
    assert "sonnet" in argv
    assert argv[-2:] == ["--foo", "bar"]


def test_pilot_doctor_reports_hosts(monkeypatch, capsys):
    from burnless import cli
    from burnless.pilot.core import HostInstallation

    monkeypatch.setattr(
        "burnless.cli.pilot_discover_hosts",
        lambda: [
            HostInstallation(name="claude", command="claude", path="/usr/bin/claude", version="claude 1.0", available=True),
            HostInstallation(name="codex", command="codex", path=None, version=None, available=False),
        ],
    )

    rc = cli.cmd_pilot(type("A", (), {"doctor": True})())
    assert rc == 0
    out = capsys.readouterr().out
    assert "Burnless pilot host probe" in out
    assert "claude" in out
    assert "codex" in out
    assert "caps: hooks=" in out


def test_pilot_uses_config_host_and_model(tmp_path, monkeypatch):
    from burnless import cli

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    (burnless_root / "config.yaml").write_text(
        """
pilot:
  host: codex
  model: codex-model
  extra_args:
    - --alpha
    - beta
""",
        encoding="utf-8",
    )

    captured = {}

    class DummyAdapter:
        def build_interactive_argv(self, root, model=None, extra_args=()):
            captured["root"] = str(root)
            captured["model"] = model
            captured["extra_args"] = list(extra_args)
            return [sys.executable, "-c", "print('ok')"]

    monkeypatch.setattr("burnless.cli.paths_mod.require_root", lambda: burnless_root)
    monkeypatch.setattr("burnless.cli.pilot_resolve_host_adapter", lambda host, **kw: DummyAdapter())
    monkeypatch.setattr("burnless.cli.pilot_run", lambda *a, **kw: 0)

    rc = cli.cmd_pilot(type("A", (), {"doctor": False, "report": False, "host": "auto", "model": None, "run_id": None, "extra_args": []})())
    assert rc == 0
    assert captured["model"] == "codex-model"
    assert captured["extra_args"] == ["--alpha", "beta"]


def test_pilot_report_summarizes_session_log(tmp_path, monkeypatch, capsys):
    from burnless import cli
    from burnless.pilot import append_session_log

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    append_session_log(
        tmp_path,
        {
            "ts": "2026-07-02T00:00:00Z",
            "run_id": "run-9",
            "host": "claude",
            "host_version": "claude 1.0",
            "old_session": "old-1",
            "new_session": "new-1",
            "strategy": "respawn",
            "context_confidence": "unknown",
            "context_before": {"current": 123, "limit": 456, "confidence": "exact"},
            "checkpoint_chars": 321,
            "pending_count": 2,
            "turns": 7,
        },
    )

    monkeypatch.setattr("burnless.cli.paths_mod.require_root", lambda: burnless_root)
    monkeypatch.setattr("burnless.cli.pilot_discover_hosts", lambda: [])
    monkeypatch.setattr(
        "burnless.cli.pilot_build_report",
        lambda *a, **kw: type(
            "R",
            (),
            {
                "capabilities": type("C", (), {"reset_strategy": "respawn"})(),
                "usage": type("U", (), {"confidence": "unknown", "current": None, "limit": None})(),
            },
        )(),
    )

    rc = cli.cmd_pilot(type("A", (), {"doctor": False, "report": True, "host": "auto", "model": None, "run_id": None, "extra_args": []})())
    assert rc == 0
    out = capsys.readouterr().out
    assert "sessions logged: 1" in out
    assert "last session: claude old-1 -> new-1" in out
    assert "last host version: claude 1.0" in out
    assert "context before: exact 123 / 456" in out
    assert "checkpoint_chars: 321" in out
    assert "pending_count: 2" in out
    assert "turns: 7" in out


def test_pilot_report_shows_run_state(tmp_path, monkeypatch, capsys):
    from burnless import cli
    from burnless.pilot import append_event

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    append_event(
        tmp_path,
        "run-9",
        {"event": "turn", "host": "claude", "host_session_id": "old-1", "process_instance_id": "proc-1"},
    )

    monkeypatch.setattr("burnless.cli.paths_mod.require_root", lambda: burnless_root)
    monkeypatch.setattr("burnless.cli.pilot_discover_hosts", lambda: [])
    monkeypatch.setattr("burnless.cli.pilot_summarize_session_log", lambda root: {"count": 0, "last": None})
    monkeypatch.setattr(
        "burnless.cli.pilot_build_report",
        lambda *a, **kw: {
            "capabilities": type("C", (), {"reset_strategy": "respawn"})(),
            "usage": type("U", (), {"confidence": "unknown", "current": None, "limit": None})(),
            "run_state": {"state": "idle", "last_event": "turn"},
        },
    )

    rc = cli.cmd_pilot(type("A", (), {"doctor": False, "report": True, "host": "auto", "model": None, "run_id": "run-9", "extra_args": []})())
    assert rc == 0
    out = capsys.readouterr().out
    assert "run_state: idle" in out


def test_pilot_auto_rollover_starts_monitor(tmp_path, monkeypatch):
    from burnless import cli

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    (burnless_root / "config.yaml").write_text(
        """
pilot:
  host: claude
  auto_rollover: true
  rollover_at_pct: 0.65
  rollover_at_tokens: 120000
  poll_interval_s: 0.01
""",
        encoding="utf-8",
    )

    class DummyAdapter:
        name = "claude"

        def build_interactive_argv(self, root, model=None, extra_args=()):
            return [sys.executable, "-c", "print('ok')"]

        def capabilities(self):
            return type("C", (), {"reset_strategy": "respawn", "supports_hooks": True, "supports_usage": True})()

        def context_usage(self, session):
            return type("U", (), {"current": 1, "limit": 1, "confidence": "unknown"})()

        def locate_session(self, run_id):
            return type("S", (), {"host": "claude", "host_session_id": run_id, "process_instance_id": run_id})()

    started = {}

    def fake_monitor(*args, **kwargs):
        started["called"] = True
        return {"checks": 1, "last": {"status": "armed"}}

    run_calls = []

    class FakeProc:
        pid = 4321

    def fake_run(argv, cwd=None, env=None, on_spawn=None):
        run_calls.append({"argv": list(argv), "env": dict(env or {})})
        if on_spawn is not None:
            on_spawn(FakeProc())
        return 0

    monkeypatch.setattr("burnless.cli.paths_mod.require_root", lambda: burnless_root)
    monkeypatch.setattr("burnless.cli.pilot_resolve_host_adapter", lambda host, **kw: DummyAdapter())
    monkeypatch.setattr("burnless.cli.pilot_run", fake_run)
    monkeypatch.setattr("burnless.cli.os.killpg", lambda *a, **kw: None)
    monkeypatch.setattr("burnless.cli.pilot_monitor_rollover_loop", fake_monitor)

    rc = cli.cmd_pilot(type("A", (), {"doctor": False, "report": False, "auto_rollover": False, "host": "auto", "model": None, "run_id": None, "extra_args": []})())
    assert rc == 0
    assert started["called"] is True
    assert len(run_calls) == 1
    assert run_calls[0]["env"]["BURNLESS_PILOT_RUN_ID"].startswith("pilot-")


def test_pilot_auto_rollover_respawns_fresh_cycle(tmp_path, monkeypatch):
    from burnless import cli

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    (burnless_root / "config.yaml").write_text(
        """
pilot:
  host: claude
  auto_rollover: true
  rollover_at_pct: 0.65
  rollover_at_tokens: 120000
  poll_interval_s: 0.01
""",
        encoding="utf-8",
    )

    class DummyAdapter:
        name = "claude"

        def build_interactive_argv(self, root, model=None, extra_args=()):
            return [sys.executable, "-c", "print('interactive')"]

        def build_fresh_argv(self, root, model=None, extra_args=()):
            return [sys.executable, "-c", "print('fresh')"]

        def capabilities(self):
            return type("C", (), {"reset_strategy": "respawn", "supports_hooks": True, "supports_usage": True})()

        def context_usage(self, session):
            return type("U", (), {"current": 1, "limit": 1, "confidence": "unknown"})()

        def locate_session(self, run_id):
            return type("S", (), {"host": "claude", "host_session_id": run_id, "process_instance_id": run_id})()

    run_calls = []
    monitor_calls = []
    kill_calls = []

    class FakeProc:
        pid = 5678

    def fake_run(argv, cwd=None, env=None, on_spawn=None):
        run_calls.append({"argv": list(argv), "env": dict(env or {})})
        if on_spawn is not None:
            on_spawn(FakeProc())
        return 0

    def fake_monitor(*args, **kwargs):
        monitor_calls.append(kwargs)
        if len(monitor_calls) == 1:
            return {"checks": 1, "last": {"status": "prepared"}, "new_session_id": "pilot-123-fresh"}
        return {"checks": 1, "last": {"status": "not_ready"}}

    monkeypatch.setattr("burnless.cli.paths_mod.require_root", lambda: burnless_root)
    monkeypatch.setattr("burnless.cli.pilot_resolve_host_adapter", lambda host, **kw: DummyAdapter())
    monkeypatch.setattr("burnless.cli.pilot_run", fake_run)
    monkeypatch.setattr("burnless.cli.pilot_monitor_rollover_loop", fake_monitor)
    monkeypatch.setattr("burnless.cli.os.killpg", lambda *args, **kwargs: kill_calls.append(args))

    rc = cli.cmd_pilot(type("A", (), {"doctor": False, "report": False, "auto_rollover": False, "host": "auto", "model": None, "run_id": "pilot-123", "extra_args": []})())
    assert rc == 0
    assert len(run_calls) == 2
    assert "interactive" in run_calls[0]["argv"][-1]
    assert "fresh" in run_calls[1]["argv"][-1]
    assert run_calls[0]["env"]["BURNLESS_PILOT_RUN_ID"] == "pilot-123"
    assert run_calls[1]["env"]["BURNLESS_PILOT_RUN_ID"] == "pilot-123"
    assert monitor_calls[0]["host_session_id"] == "pilot-123"
    assert monitor_calls[0]["new_session_id"] == "pilot-123-fresh"
    assert monitor_calls[1]["host_session_id"] == "pilot-123-fresh"
    assert kill_calls


def test_pilot_auto_prompts_for_host_choice_and_persists(tmp_path, monkeypatch, capsys):
    from burnless import cli
    from burnless.pilot.core import HostInstallation

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    (burnless_root / "config.yaml").write_text(
        """
pilot:
  host: auto
  auto_rollover: false
""",
        encoding="utf-8",
    )

    chosen = {}

    class DummyAdapter:
        name = "codex"

        def build_interactive_argv(self, root, model=None, extra_args=()):
            return [sys.executable, "-c", "print('ok')"]

        def capabilities(self):
            return type("C", (), {"reset_strategy": "respawn"})()

        def context_usage(self, session):
            return type("U", (), {"current": None, "limit": None, "confidence": "unknown"})()

        def locate_session(self, run_id):
            return type("S", (), {"host": "codex", "host_session_id": run_id, "process_instance_id": run_id})()

    monkeypatch.setattr(
        "burnless.cli.pilot_discover_hosts",
        lambda: [
            HostInstallation(name="claude", command="claude", path="/usr/bin/claude", version="claude 1.0", available=True),
            HostInstallation(name="codex", command="codex", path="/usr/bin/codex", version="codex 1.0", available=True),
        ],
    )
    monkeypatch.setattr("burnless.cli.paths_mod.require_root", lambda: burnless_root)
    monkeypatch.setattr("burnless.cli.sys.stdin", type("S", (), {"isatty": lambda self: True})())
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")
    def fake_resolve(host, **kw):
        chosen["host"] = host
        return DummyAdapter()

    monkeypatch.setattr("burnless.cli.pilot_resolve_host_adapter", fake_resolve)
    monkeypatch.setattr("burnless.cli.pilot_run", lambda *a, **kw: 0)

    rc = cli.cmd_pilot(type("A", (), {"doctor": False, "report": False, "host": "auto", "model": None, "run_id": None, "extra_args": []})())
    assert rc == 0
    out = capsys.readouterr().out
    assert "host selected -> codex" in out
    assert chosen["host"] == "codex"
    updated = yaml.safe_load((burnless_root / "config.yaml").read_text(encoding="utf-8"))
    assert updated["pilot"]["host"] == "codex"


def test_pilot_auto_falls_back_noninteractive_without_exiting(tmp_path, monkeypatch, capsys):
    from burnless import cli
    from burnless.pilot.core import HostInstallation

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    (burnless_root / "config.yaml").write_text(
        """
pilot:
  host: auto
  auto_rollover: false
""",
        encoding="utf-8",
    )

    chosen = {}

    class DummyAdapter:
        name = "claude"

        def build_interactive_argv(self, root, model=None, extra_args=()):
            return [sys.executable, "-c", "print('ok')"]

        def capabilities(self):
            return type("C", (), {"reset_strategy": "respawn"})()

        def context_usage(self, session):
            return type("U", (), {"current": None, "limit": None, "confidence": "unknown"})()

        def locate_session(self, run_id):
            return type("S", (), {"host": "claude", "host_session_id": run_id, "process_instance_id": run_id})()

    monkeypatch.setattr(
        "burnless.cli.pilot_discover_hosts",
        lambda: [
            HostInstallation(name="claude", command="claude", path="/usr/bin/claude", version="claude 1.0", available=True),
            HostInstallation(name="codex", command="codex", path="/usr/bin/codex", version="codex 1.0", available=True),
        ],
    )
    monkeypatch.setattr("burnless.cli.paths_mod.require_root", lambda: burnless_root)
    monkeypatch.setattr("burnless.cli.sys.stdin", type("S", (), {"isatty": lambda self: False})())

    def fake_resolve(host, **kw):
        chosen["host"] = host
        return DummyAdapter()

    monkeypatch.setattr("burnless.cli.pilot_resolve_host_adapter", fake_resolve)
    monkeypatch.setattr("burnless.cli.pilot_run", lambda *a, **kw: 0)

    rc = cli.cmd_pilot(type("A", (), {"doctor": False, "report": False, "host": "auto", "model": None, "run_id": None, "extra_args": []})())
    assert rc == 0
    out = capsys.readouterr()
    assert "non-interactive launch detected" in out.err
    assert chosen["host"] == "claude"


def test_monitor_rollover_loop_prepares_when_idle_and_above_threshold(tmp_path):
    from burnless import recovery
    from burnless.pilot import append_event, monitor_rollover_loop
    from burnless.pilot.core import ContextUsage

    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="old-sid",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- pronto\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
        journal_head=0,
    )
    recovery.journal_append(
        root,
        {
            "exchange_id": "x1",
            "host": "claude",
            "host_session_id": "old-sid",
            "process_instance_id": "proc-1",
            "user_text": "pergunta",
            "assistant_text": "resposta",
            "files": [],
        },
    )
    append_event(root, "run-1", {"event": "turn", "host": "claude", "host_session_id": "old-sid", "process_instance_id": "proc-1"})
    append_event(root, "run-1", {"event": "turn_end", "host": "claude", "host_session_id": "old-sid", "process_instance_id": "proc-1"})

    result = monitor_rollover_loop(
        root,
        host="claude",
        host_session_id="old-sid",
        process_instance_id="proc-1",
        run_id="run-1",
        new_session_id="new-sid",
        context_usage_fn=lambda: ContextUsage(current=900, limit=1000, confidence="exact"),
        rollover_at_pct=0.65,
        rollover_at_tokens=1200,
        delta_budget_tokens=1500,
        poll_interval_s=0.01,
        max_checks=1,
    )
    assert result["checks"] == 1
    assert result["last"]["status"] == "prepared"
    assert result["last"]["prepared"]["status"] == "ready"
    assert result["last"]["prepared"]["restore"] is not None


def test_pilot_events_roundtrip(tmp_path):
    from burnless.pilot import append_event, normalize_and_append_event, read_events
    from burnless.pilot.core import PilotEvent
    from burnless.pilot.hosts.claude import ClaudeAdapter

    root = tmp_path
    append_event(
        root,
        "run-1",
        PilotEvent(
            host="claude",
            host_session_id="sess-1",
            process_instance_id="proc-1",
            event="turn",
            source="hook",
            cwd=str(tmp_path),
            user_text="hello",
            usage={"input_tokens": 3},
        ),
    )
    rows = read_events(root, "run-1")
    assert len(rows) == 1
    assert rows[0]["host"] == "claude"
    assert rows[0]["usage"]["input_tokens"] == 3

    payload = {"hookEventName": "SessionEnd", "session_id": "sess-2", "process_instance_id": "proc-2"}
    event = normalize_and_append_event(root, "run-1", ClaudeAdapter(), payload)
    assert event.event == "SessionEnd"
    rows = read_events(root, "run-1")
    assert rows[-1]["event"] == "SessionEnd"


def test_fake_tui_host_emits_reset_and_events(tmp_path):
    script = Path(__file__).resolve().parent / "fixtures" / "fake_tui_host.py"
    events = tmp_path / "events.jsonl"
    proc = __import__("subprocess").run(
        [sys.executable, str(script)],
        input="/hello\n/clear\n/exit\n",
        text=True,
        capture_output=True,
        env={**os.environ, "FAKE_TUI_EVENTS_PATH": str(events), "FAKE_TUI_SESSION_ID": "sess-9"},
        check=False,
    )
    assert proc.returncode == 0
    assert "\x1b[?1049h" in proc.stdout
    assert "reset" in proc.stdout
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["event"] for row in rows] == ["turn", "session_reset", "turn_end"]


def test_session_log_summary(tmp_path):
    from burnless.pilot import append_session_log, summarize_session_log

    append_session_log(
        tmp_path,
        {"ts": "2026-07-02T00:00:00Z", "run_id": "r1", "host": "codex", "old_session": "s1", "new_session": "s2", "strategy": "respawn"},
    )
    summary = summarize_session_log(tmp_path)
    assert summary["count"] == 1
    assert summary["host"] == "codex"
    assert summary["strategy"] == "respawn"


def test_run_event_summary_detects_idle_and_active(tmp_path):
    from burnless.pilot import append_event, summarize_run_events

    root = tmp_path
    append_event(root, "r1", {"event": "turn_start", "host": "claude", "host_session_id": "s1", "process_instance_id": "p1"})
    summary = summarize_run_events(root, "r1")
    assert summary["state"] == "active"

    append_event(root, "r2", {"event": "turn", "host": "claude", "host_session_id": "s2", "process_instance_id": "p2"})
    summary2 = summarize_run_events(root, "r2")
    assert summary2["idle"] is True
    assert summary2["state"] == "idle"


def test_pilot_rollover_bridge_renders_restore(tmp_path):
    from burnless import recovery
    from burnless.pilot import append_event, render_restore, claim_handoff, prepare_rollover

    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="old-sid",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=1,
        journal_head=1,
    )
    recovery.journal_append(
        root,
        {
            "exchange_id": "x1",
            "host": "claude",
            "host_session_id": "old-sid",
            "process_instance_id": "proc-1",
            "user_text": "pergunta",
            "assistant_text": "resposta",
            "files": [],
        },
    )
    append_event(root, "run-1", {"event": "turn", "host": "claude", "host_session_id": "old-sid", "process_instance_id": "proc-1"})
    handoff = recovery.write_handoff(root, host="claude", host_session_id="old-sid", process_instance_id="proc-1")
    assert handoff["journal_head"] == 1
    claimed = claim_handoff(root, host="claude", process_instance_id="proc-1", new_session_id="new-sid")
    assert claimed and claimed["claimed_by"] == "new-sid"

    payload = render_restore(
        root,
        host="claude",
        host_session_id="old-sid",
        process_instance_id="proc-1",
        new_session_id="new-sid",
        source="clear",
        budget_tokens=1000,
    )
    assert payload is not None
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "objetivo vivo" in ctx
    assert "old-sid" in ctx

    ready = prepare_rollover(
        root,
        host="claude",
        host_session_id="old-sid",
        process_instance_id="proc-1",
        run_id="run-1",
        new_session_id="new-sid",
    )
    assert ready["status"] == "ready"
    assert ready["restore"] is not None


def test_prepare_rollover_blocks_on_active_run(tmp_path):
    from burnless.pilot import append_event, prepare_rollover

    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    append_event(root, "run-1", {"event": "turn_start", "host": "claude", "host_session_id": "old-sid", "process_instance_id": "proc-1"})

    result = prepare_rollover(
        root,
        host="claude",
        host_session_id="old-sid",
        process_instance_id="proc-1",
        run_id="run-1",
        new_session_id="new-sid",
    )
    assert result["status"] == "not_ready"
    assert result["reason"] == "run_not_idle"


def test_evaluate_rollover_blocks_when_usage_unknown(tmp_path):
    from burnless.pilot import append_event, evaluate_rollover

    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    append_event(root, "run-1", {"event": "turn", "host": "claude", "host_session_id": "old-sid", "process_instance_id": "proc-1"})
    append_event(root, "run-1", {"event": "turn_end", "host": "claude", "host_session_id": "old-sid", "process_instance_id": "proc-1"})

    result = evaluate_rollover(
        root,
        host="claude",
        host_session_id="old-sid",
        process_instance_id="proc-1",
        run_id="run-1",
        new_session_id="new-sid",
    )
    assert result["should_rollover"] is False
    assert result["reason"] == "usage_unknown"


def test_evaluate_rollover_triggers_above_threshold(tmp_path):
    from burnless.pilot import append_event, evaluate_rollover
    from burnless.pilot.core import ContextUsage
    from burnless import recovery

    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="old-sid",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- pronto\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
        journal_head=0,
    )
    recovery.journal_append(
        root,
        {
            "exchange_id": "x1",
            "host": "claude",
            "host_session_id": "old-sid",
            "process_instance_id": "proc-1",
            "user_text": "pergunta",
            "assistant_text": "resposta",
            "files": [],
        },
    )
    append_event(root, "run-1", {"event": "turn", "host": "claude", "host_session_id": "old-sid", "process_instance_id": "proc-1"})
    append_event(root, "run-1", {"event": "turn_end", "host": "claude", "host_session_id": "old-sid", "process_instance_id": "proc-1"})

    result = evaluate_rollover(
        root,
        host="claude",
        host_session_id="old-sid",
        process_instance_id="proc-1",
        run_id="run-1",
        new_session_id="new-sid",
        context_usage=ContextUsage(current=900, limit=1000, confidence="exact"),
        rollover_at_pct=0.65,
        rollover_at_tokens=1200,
        delta_budget_tokens=1500,
    )
    assert result["should_rollover"] is True
    assert result["reason"] == "threshold_reached"
    assert result["prepared"]["status"] == "ready"


def test_session_log_serializes_dataclass_usage_without_crashing(tmp_path):
    """Regression (2026-07-03): a rollover decision carrying ContextUsage
    inside the session-log row raised TypeError in json.dumps and killed the
    pilot right after the monitor stopped the child — suicide by telemetry."""
    from burnless.pilot import append_session_log
    from burnless.pilot.core import ContextUsage
    from burnless.pilot.events import read_session_log

    row = {
        "ts": "2026-07-03T00:00:00Z",
        "run_id": "pilot-x",
        "rollover": {
            "last": {
                "status": "prepared",
                "decision": {
                    "should_rollover": True,
                    "usage": ContextUsage(current=140000, limit=200000, confidence="estimated"),
                },
            },
        },
    }
    append_session_log(tmp_path, row)

    rows = read_session_log(tmp_path)
    assert len(rows) == 1
    usage = rows[0]["rollover"]["last"]["decision"]["usage"]
    assert usage["current"] == 140000
    assert usage["confidence"] == "estimated"


def test_version_for_times_out_and_caches(monkeypatch, tmp_path):
    """Regression (2026-07-03): `claude --version` hung with no timeout and
    froze the pilot on the hot path. Must bound the call, cache the result,
    and fail to None instead of hanging."""
    import subprocess as sp
    from burnless.pilot import core

    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nsleep 60\n")
    fake.chmod(0o755)

    core._VERSION_CACHE.clear()
    monkeypatch.setattr(core.shutil, "which", lambda _cmd: str(fake))
    monkeypatch.setattr(core, "_VERSION_TIMEOUT_S", 1)

    import time as _time

    start = _time.time()
    assert core._version_for("claude") is None
    assert _time.time() - start < 10

    calls = {"n": 0}
    real_run = sp.run

    def counting_run(*args, **kwargs):
        calls["n"] += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", counting_run)
    assert core._version_for("claude") is None  # cached: no second subprocess
    assert calls["n"] == 0
    core._VERSION_CACHE.clear()
