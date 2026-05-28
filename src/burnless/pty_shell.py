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
from . import subscription_usage as sub_usage_mod
from . import usage_meter as usage_meter_mod


_PROVIDER_BINS: dict[str, list[str]] = {
    "anthropic": ["claude"],
    "openai": ["codex"],
    "codex": ["codex"],
    "ollama": ["ollama", "run"],
}


def _maestro_argv(cfg: dict) -> list[str]:
    maestro_cfg = cfg.get("brain", {})  # legacy persisted key name (kept for on-disk back-compat); represents the Maestro layer
    provider = maestro_cfg.get("provider", "anthropic")
    model = maestro_cfg.get("model")
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


def _status_line(tokens: int, delegations: int, bin_name: str, hint: str = "",
                 burst_delta: int = 0) -> str:
    """Format the status bar. When burst_delta > 0, append a bright +N badge."""
    base = f"🔥 {tokens:,} burnless tokens · {delegations} delegations · {bin_name}"
    if burst_delta > 0:
        # Bright orange burst marker (ANSI 208 ≈ orange)
        burst = f" \033[38;5;208m+{burst_delta:,}\033[33m"
        base = base + burst
    if hint:
        base = f"{base} · {hint}"
    return base


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

    _debug = os.environ.get("BURNLESS_PTY_DEBUG") == "1"
    _debug_log = Path("/tmp/burnless_pty_debug.log") if _debug else None

    def _dlog(msg: str) -> None:
        if _debug_log:
            try:
                with _debug_log.open("a", encoding="utf-8") as f:
                    f.write(f"{time.time():.3f} {msg}\n")
            except OSError:
                pass

    _PRO_TIPS = [
        "Pro: capsules + index server-side · O(N) cross-session",
        "Pro: bruto nunca persiste no server (zero-retention)",
        "Pro: nightly consolidation pelo seu Bronze local",
        "Pro: cache hot compartilhado entre devices",
        "Free comprova. Pro escala.",
        "Pro: PageRank cross-capsule emerge do uso",
    ]

    _session = {
        "last_metrics_mtime": 0.0,
        "last_tokens": 0,
        "burst_until": 0.0,
        "burst_delta": 0,
        "tip_index": 0,
        "tip_last_swap": time.time(),
        "last_usage_ts": 0.0,
        "last_usage_hint": "",
        "last_quota_ts": 0.0,
        "last_quota_hint": "",
    }
    _TIP_SWAP_EVERY = 10.0   # seconds
    _BURST_DURATION = 1.0    # seconds the orange "+N" stays visible
    _USAGE_TTL = 2.0         # seconds
    _QUOTA_TTL = 15.0        # seconds

    _quota = sub_usage_mod.UsagePoller(ttl_s=60)

    def _fmt_reset_epoch(ts_raw: str | None) -> str:
        if not ts_raw:
            return ""
        try:
            ts = int(ts_raw)
            secs = ts - int(time.time())
            if secs <= 0:
                return "resetting…"
            if secs < 3600:
                m, s = divmod(secs, 60)
                return f"resets {m}m{s:02d}s"
            if secs < 86400:
                h = secs // 3600
                m = (secs % 3600) // 60
                return f"resets {h}h{m:02d}m"
            return "resets >1d"
        except Exception:
            return ""

    # Matches CSI scroll-region commands (ESC [ <opt numbers> r) AND
    # screen clears that imply terminal reset (ESC [ 2J, ESC [ 3J, ESC [ H+2J).
    _SCROLL_RE = _re.compile(rb"\x1b\[[\d;]*r")
    _CLEAR_RE = _re.compile(rb"\x1b\[H\x1b\[[23]J|\x1b\[[23]J")

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
        # Pull burst + tip from session
        now = time.time()
        burst_delta = _session["burst_delta"] if now < _session["burst_until"] else 0
        # Rotate tip every _TIP_SWAP_EVERY seconds
        if now - _session["tip_last_swap"] >= _TIP_SWAP_EVERY:
            _session["tip_index"] = (_session["tip_index"] + 1) % len(_PRO_TIPS)
            _session["tip_last_swap"] = now
        active_hint = _PRO_TIPS[_session["tip_index"]] if hint == "" else hint

        # Optional: show live cache usage from Claude Code JSONL (codeburn-style source),
        # without depending on codeburn itself.
        if os.environ.get("BURNLESS_PTY_USAGE", "1").strip() != "0":
            now2 = time.time()
            if now2 - float(_session.get("last_usage_ts", 0.0) or 0.0) >= _USAGE_TTL:
                try:
                    d = usage_meter_mod.claude_usage_delta(window_seconds=15 * 60)
                    _session["last_usage_hint"] = usage_meter_mod.fmt_compact(d)
                except Exception:
                    _session["last_usage_hint"] = ""
                _session["last_usage_ts"] = now2

            # If unified quota headers are available (monthly plan), prefer showing
            # quota % + reset ETA; it matches user mental model (“avoid hitting cap”).
            if os.environ.get("BURNLESS_PTY_QUOTA", "1").strip() != "0":
                if now2 - float(_session.get("last_quota_ts", 0.0) or 0.0) >= _QUOTA_TTL:
                    try:
                        u = _quota.get()
                        if u and u.u5h is not None:
                            pct = int(max(0.0, min(1.0, float(u.u5h))) * 100)
                            reset = _fmt_reset_epoch(u.r5h)
                            _session["last_quota_hint"] = f"quota {pct}%{(' · ' + reset) if reset else ''}"
                        else:
                            _session["last_quota_hint"] = ""
                    except Exception:
                        _session["last_quota_hint"] = ""
                    _session["last_quota_ts"] = now2

            if _session.get("last_quota_hint"):
                # Show quota first, then cache-spared tokens for intuition.
                if _session.get("last_usage_hint"):
                    active_hint = f"{_session['last_quota_hint']} · {_session['last_usage_hint']}"
                else:
                    active_hint = _session["last_quota_hint"]
            elif _session.get("last_usage_hint"):
                active_hint = _session["last_usage_hint"]

        line = _status_line(tokens, delegations, bin_name, active_hint, burst_delta)[:cur_cols - 1]
        title_text = f"\U0001f525 {tokens:,} bt · {delegations}d"
        bar = (
            f"\0337"
            f"\033[1;{pty_rows}r"
            f"\033[{cur_rows};1H"
            f"\033[2K"
            f"\033[33m{line}\033[0m"
            f"\033]0;{title_text}\007"
            f"\0338"
        ).encode()
        _write_out(bar)

    def _reader() -> None:
        """Forward PTY output, intercepting scroll-region resets and screen clears."""
        while not stop_ev.is_set():
            try:
                data = spawn.read(4096)
            except (EOFError, OSError):
                break
            # Replace any \033[...r with our constrained scroll region
            filtered = _SCROLL_RE.sub(_scroll_region_bytes(), data)
            had_clear = bool(_CLEAR_RE.search(filtered))
            _write_out(filtered)
            if had_clear:
                _dlog("clear detected, redrawing")
                # Claude cleared screen — re-assert scroll region + redraw status atomically
                try:
                    ts4 = os.get_terminal_size()
                    _write_out(_scroll_region_bytes())
                    _draw_status(ts4.lines, ts4.columns)
                except OSError:
                    pass

    def _poll_metrics_for_burst() -> None:
        """Watch metrics file mtime; trigger burst on detected token delta."""
        if metrics_path is None or not metrics_path.exists():
            return
        try:
            mtime = metrics_path.stat().st_mtime
        except OSError:
            return
        if mtime <= _session["last_metrics_mtime"]:
            return
        _session["last_metrics_mtime"] = mtime
        tokens, _ = _read_metrics(metrics_path)
        delta = tokens - _session["last_tokens"]
        _session["last_tokens"] = tokens
        if delta > 0:
            _session["burst_delta"] = delta
            _session["burst_until"] = time.time() + _BURST_DURATION
            if _debug_log:
                _dlog(f"burst triggered: +{delta} tokens")

    def _status_updater() -> None:
        # Refresh 5x/s so a stale bar self-corrects within 200ms after any glitch.
        # Cheap: only writes ~200 bytes per tick.
        while not stop_ev.is_set():
            try:
                _poll_metrics_for_burst()
                ts2 = os.get_terminal_size()
                _draw_status(ts2.lines, ts2.columns)
            except OSError:
                pass
            time.sleep(0.2)

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
