"""Background daemon that keeps warm pools (claude + codex) hot.

Polls `.burnless/warm_session*.json` states every poll_interval_s, calls
refresh() on any pool that needs_refresh() and is still alive (within TTL).
Logs to .burnless/warm_daemon.log; honors SIGTERM/SIGINT gracefully.

Spawn via:
  burnless warm daemon start   # subprocess.Popen(start_new_session=True)
Stop via:
  burnless warm daemon stop    # reads PID file, sends SIGTERM
Foreground (debug):
  burnless warm daemon run-fg
"""
from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config as config_mod
from . import warm_session as ws_claude
from . import warm_session_codex as ws_codex


DEFAULT_POLL_INTERVAL_S = 60
LOG_MAX_BYTES = 500_000  # rotate at ~500KB


def pid_file_path(burnless_root: Path) -> Path:
    return Path(burnless_root) / "warm_daemon.pid"


def log_file_path(burnless_root: Path) -> Path:
    return Path(burnless_root) / "warm_daemon.log"


def is_running(burnless_root: Path) -> tuple[bool, int | None]:
    """Return (alive, pid). alive=True if PID file points to a live process."""
    pf = pid_file_path(burnless_root)
    if not pf.exists():
        return (False, None)
    try:
        pid = int(pf.read_text().strip())
    except (ValueError, OSError):
        return (False, None)
    try:
        os.kill(pid, 0)  # signal 0 = test if process exists
        return (True, pid)
    except OSError:
        return (False, pid)


def write_pid(burnless_root: Path, pid: int) -> None:
    pf = pid_file_path(burnless_root)
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid))


def clear_pid(burnless_root: Path) -> None:
    pf = pid_file_path(burnless_root)
    try:
        pf.unlink()
    except OSError:
        pass


def _log(burnless_root: Path, msg: str) -> None:
    log_path = log_file_path(burnless_root)
    ts = datetime.now(timezone.utc).isoformat()
    line = f"{ts} {msg}\n"
    try:
        # Rotate if needed
        if log_path.exists() and log_path.stat().st_size > LOG_MAX_BYTES:
            log_path.replace(log_path.with_suffix(".log.1"))
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _extract_claude_model(cfg: dict) -> str | None:
    """Parse --model X from agents.silver.command (or gold/bronze)."""
    for tier in ("silver", "gold", "bronze"):
        cmd = ((cfg.get("agents") or {}).get(tier) or {}).get("command", "")
        m = re.search(r"--model\s+(\S+)", cmd)
        if m:
            return m.group(1)
    return "claude-sonnet-4-6"


def _extract_codex_model(cfg: dict) -> str | None:
    for tier in ("silver", "gold", "bronze"):
        cmd = ((cfg.get("agents") or {}).get(tier) or {}).get("command", "")
        m = re.search(r"\b-m\s+(\S+)", cmd)
        if m:
            return m.group(1)
    return "gpt-5.2"


def _maybe_refresh(burnless_root: Path, cfg: dict) -> None:
    """Run one poll iteration: refresh both warm pools if needed."""
    # claude
    try:
        if ws_claude.is_alive(burnless_root) and ws_claude.needs_refresh(burnless_root):
            model = _extract_claude_model(cfg) or "claude-sonnet-4-6"
            ws_claude.refresh(burnless_root, model=model)
            _log(burnless_root, f"claude refresh OK (model={model})")
    except Exception as e:
        _log(burnless_root, f"claude refresh ERR: {e}")
    # codex
    try:
        if ws_codex.is_alive(burnless_root) and ws_codex.needs_refresh(burnless_root):
            model = _extract_codex_model(cfg) or "gpt-5.2"
            ws_codex.refresh(burnless_root, model=model)
            _log(burnless_root, f"codex refresh OK (model={model})")
    except Exception as e:
        _log(burnless_root, f"codex refresh ERR: {e}")


def run_loop(burnless_root: Path) -> int:
    """Main daemon loop. Returns exit code."""
    cfg = config_mod.load(burnless_root / "config.yaml")
    poll_s = int(((cfg.get("warm") or {}).get("daemon") or {}).get("poll_interval_s", DEFAULT_POLL_INTERVAL_S))

    write_pid(burnless_root, os.getpid())
    _log(burnless_root, f"daemon start pid={os.getpid()} poll={poll_s}s")

    stopping = False

    def _sigterm(signum, frame):
        nonlocal stopping
        stopping = True
        _log(burnless_root, f"signal {signum} received, stopping")

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    try:
        while not stopping:
            _maybe_refresh(burnless_root, cfg)
            # Sleep in small steps so SIGTERM is responsive
            for _ in range(poll_s):
                if stopping:
                    break
                time.sleep(1)
    finally:
        clear_pid(burnless_root)
        _log(burnless_root, "daemon stopped")
    return 0
