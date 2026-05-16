"""burnless pty — PTY wrapper that spawns the real maestro CLI.

v0.1: real ptyprocess wrapper with live status bar at bottom.
Fallback to os.execvp if ptyprocess not installed.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from . import __version__
from . import config as config_mod
from . import metrics as metrics_mod
from . import paths as paths_mod


_PROVIDER_BINS: dict[str, list[str]] = {
    "anthropic": ["claude"],
    "openai": ["codex"],
    "codex": ["codex"],
    "ollama": ["ollama", "run"],
}


def _maestro_argv(cfg: dict) -> list[str]:
    brain = cfg.get("brain", {})
    provider = brain.get("provider", "anthropic")
    model = brain.get("model")
    base = _PROVIDER_BINS.get(provider, ["claude"])
    if provider == "anthropic":
        return base
    if provider in ("openai", "codex"):
        return base + (["-m", model] if model else [])
    if provider == "ollama":
        return base + [model or "mistral"]
    return base


def _read_metrics(metrics_path: Path) -> tuple[int, int]:
    try:
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
        return int(data.get("burnless_tokens", 0)), int(data.get("delegation_counter", 0) or 0)
    except Exception:
        return 0, 0


def _status_line(tokens: int, delegations: int, bin_name: str, hint: str = "") -> str:
    base = f"🔥 {tokens:,} burnless tokens · {delegations} delegations · {bin_name}"
    return f"{base} · {hint}" if hint else base


def _run_pty(maestro_argv: list[str], metrics_path: Path | None, bin_name: str, hint: str = "") -> int:
    try:
        import ptyprocess
    except ImportError:
        sys.stderr.write(
            "burnless: ptyprocess not installed — status bar unavailable.\n"
            "Install with: pip install ptyprocess\n\n"
        )
        os.execvp(maestro_argv[0], maestro_argv)
        return 1

    import re as _re

    try:
        ts = os.get_terminal_size()
        cols, rows = ts.columns, ts.lines
    except OSError:
        cols, rows = 220, 50

    pty_rows = max(rows - 1, 10)
    fd_out = sys.stdout.fileno()
    _out_lock = threading.Lock()

    # Matches any CSI scroll-region command: ESC [ <opt numbers> r
    _SCROLL_RE = _re.compile(rb"\x1b\[[\d;]*r")

    def _scroll_region_bytes() -> bytes:
        return f"\033[1;{pty_rows}r".encode()

    def _write_out(data: bytes) -> None:
        with _out_lock:
            try:
                os.write(fd_out, data)
            except OSError:
                pass

    def _draw_status(cur_rows: int, cur_cols: int) -> None:
        tokens, delegations = 0, 0
        if metrics_path:
            tokens, delegations = _read_metrics(metrics_path)
        line = _status_line(tokens, delegations, bin_name, hint)[:cur_cols - 1]
        title_text = f"\U0001f525 {tokens:,} bt · {delegations}d" + (f" · {hint}" if hint else "")
        bar = (
            f"\0337"                           # DEC save cursor
            f"\033[1;{pty_rows}r"              # re-assert scroll region
            f"\033[{cur_rows};1H"              # jump to last (unprotected) row
            f"\033[2K"                         # clear
            f"\033[33m{line}\033[0m"           # yellow status text
            f"\033]0;{title_text}\007"         # terminal title (survives Claude clear)
            f"\0338"                           # DEC restore cursor
        ).encode()
        _write_out(bar)

    def _reader() -> None:
        """Forward PTY output, intercepting scroll-region resets."""
        while not stop_ev.is_set():
            try:
                data = spawn.read(4096)
            except (EOFError, OSError):
                break
            # Replace any \033[...r with our constrained scroll region
            filtered = _SCROLL_RE.sub(_scroll_region_bytes(), data)
            _write_out(filtered)

    def _status_updater() -> None:
        while not stop_ev.is_set():
            try:
                ts2 = os.get_terminal_size()
                _draw_status(ts2.lines, ts2.columns)
            except OSError:
                pass
            time.sleep(1)

    stop_ev = threading.Event()

    def _sigwinch_handler(signum: int, frame: object) -> None:
        nonlocal pty_rows, rows, cols
        try:
            ts3 = os.get_terminal_size()
            rows, cols = ts3.lines, ts3.columns
            pty_rows = max(rows - 1, 10)
            spawn.setwinsize(pty_rows, cols)
            _write_out(_scroll_region_bytes())
            _draw_status(rows, cols)
        except Exception:
            pass

    # Apply scroll region before spawning so initial clear by Claude stays inside it
    _write_out(_scroll_region_bytes())

    spawn = ptyprocess.PtyProcess.spawn(maestro_argv, dimensions=(pty_rows, cols))

    signal.signal(signal.SIGWINCH, _sigwinch_handler)

    _draw_status(rows, cols)

    reader_t = threading.Thread(target=_reader, daemon=True)
    status_t = threading.Thread(target=_status_updater, daemon=True)
    reader_t.start()
    status_t.start()

    import select
    import tty
    import termios
    fd_in = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd_in)
    try:
        tty.setraw(fd_in)
        while spawn.isalive():
            # Non-blocking check so we exit promptly when Claude quits
            ready, _, _ = select.select([fd_in], [], [], 0.1)
            if not ready:
                continue
            try:
                data = os.read(fd_in, 256)
                if not data:
                    break
                spawn.write(data)
            except (EOFError, OSError):
                break
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd_in, termios.TCSADRAIN, old_settings)
        stop_ev.set()

    # Reset scroll region, clear screen, restore cursor to top
    try:
        _write_out(b"\033[r\x1b[2J\x1b[H")
    except Exception:
        pass

    spawn.wait()
    return spawn.exitstatus or 0


def main(argv_extra: list[str] | None = None) -> int:
    root = paths_mod.find_root()

    cfg: dict = {}
    metrics_path: Path | None = None
    if root:
        p = paths_mod.paths_for(root)
        cfg = config_mod.load(p["config"])
        metrics_path = p["metrics"]

    metrics: dict = {}
    if metrics_path and metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    maestro_argv = _maestro_argv(cfg)
    bin_name = maestro_argv[0]
    project = cfg.get("project_name") or (root.parent.name if root else "burnless")
    burnless_tokens = int(metrics.get("burnless_tokens", 0))
    delegations = int(metrics.get("delegation_counter", 0) or 0)
    model = cfg.get("brain", {}).get("model", "")

    # Efficiency hint — opaque estimate for Free, no formula exposed
    plan = cfg.get("plan", "free")
    hint = ""
    if plan == "free":
        ratio = cfg.get("metrics", {}).get("token_estimation_ratio", 4)
        eff = max(1.1, ratio / 2.0)
        hint = f"~{eff:.0f}x est.  \033[2m↑ Pro: savings reais\033[0m"

    is_tty = sys.stdout.isatty()

    full_argv = maestro_argv + (argv_extra or [])

    if not is_tty:
        # Non-interactive (tests, pipes): plain exec
        try:
            os.execvp(full_argv[0], full_argv)
        except FileNotFoundError:
            sys.stderr.write(f"burnless pty: '{full_argv[0]}' not found\n")
            return 1

    # Splash: centered header for 2s, then clear before Claude loads
    try:
        ts = os.get_terminal_size()
        term_cols, term_rows = ts.columns, ts.lines
    except OSError:
        term_cols, term_rows = 80, 24

    # Strip ANSI for plain hint in splash
    import re as _re
    _ansi_re = _re.compile(r"\033\[[^m]*m|\033\][^\007]*\007")
    plain_hint = _ansi_re.sub("", hint)
    lines = [
        f"\033[33m🔥  Burnless v{__version__}\033[0m",
        f"maestro: \033[1m{bin_name}\033[0m" + (f"  ({model})" if model else "") + "  compression: balanced",
        f"\033[1m{burnless_tokens:,}\033[0m burnless tokens · {delegations} delegations · {project}",
        f"\033[2m{plain_hint}\033[0m" if plain_hint else "",
    ]
    lines = [l for l in lines if l]  # drop empty
    start_row = max(1, term_rows // 2 - len(lines) // 2)

    out = "\x1b[2J\x1b[H"  # clear
    for i, line in enumerate(lines):
        plain_len = len(_ansi_re.sub("", line))
        pad = max(0, (term_cols - plain_len) // 2)
        out += f"\033[{start_row + i};{pad + 1}H{line}"
    sys.stdout.write(out)
    sys.stdout.flush()
    time.sleep(2)
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()

    return _run_pty(full_argv, metrics_path, bin_name, hint)
