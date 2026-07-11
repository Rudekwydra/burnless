from __future__ import annotations

import contextlib
import os
import pty
import select
import signal
import subprocess
import sys
import termios
import struct
import fcntl
import time
from pathlib import Path
from typing import Callable, Iterable


def _set_raw(fd: int):
    old = termios.tcgetattr(fd)
    raw = termios.tcgetattr(fd)
    raw[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
    raw[1] &= ~termios.OPOST
    raw[0] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON)
    raw[2] |= termios.CS8
    termios.tcsetattr(fd, termios.TCSANOW, raw)
    return old


def run_pilot(
    argv: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = False,
    input_bytes: bytes | None = None,
    on_spawn=None,
    title_provider: Callable[[], str] | None = None,
    title_interval_s: float = 5.0,
) -> int | tuple[int, str]:
    if capture:
        proc = subprocess.run(argv, cwd=cwd, env=env, input=input_bytes, capture_output=True)
        output = (proc.stdout or b"").decode("utf-8", "replace") + (proc.stderr or b"").decode("utf-8", "replace")
        return proc.returncode, output

    master_fd, slave_fd = pty.openpty()
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    old_tty = None
    if os.isatty(stdin_fd):
        old_tty = _set_raw(stdin_fd)

    try:
        proc = subprocess.Popen(
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave_fd)
        if on_spawn is not None:
            try:
                on_spawn(proc)
            except Exception:
                pass

        last_title_emit = time.monotonic()
        if title_provider is not None and not os.isatty(stdout_fd):
            title_provider = None  # never leak OSC bytes into piped output
        if title_provider is not None:
            try:
                from . import hud as hud_mod
                title_bytes = hud_mod.osc_title(title_provider())
                with contextlib.suppress(Exception):
                    os.write(stdout_fd, title_bytes)
            except Exception:
                pass

        def _forward_winch(*_args):
            with contextlib.suppress(Exception):
                rows_cols = struct.pack("hhhh", 0, 0, 0, 0)
                winsize = fcntl.ioctl(stdin_fd, termios.TIOCGWINSZ, rows_cols)
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                os.killpg(proc.pid, signal.SIGWINCH)

        old_winch = signal.getsignal(signal.SIGWINCH)
        signal.signal(signal.SIGWINCH, _forward_winch)
        _forward_winch()

        if input_bytes:
            os.write(master_fd, input_bytes)
        while True:
            rlist = [master_fd]
            if os.isatty(stdin_fd):
                rlist.append(stdin_fd)
            ready, _, _ = select.select(rlist, [], [], 0.1)

            if title_provider is not None:
                now = time.monotonic()
                if now - last_title_emit >= title_interval_s:
                    try:
                        from . import hud as hud_mod
                        title_bytes = hud_mod.osc_title(title_provider())
                        with contextlib.suppress(Exception):
                            os.write(stdout_fd, title_bytes)
                    except Exception:
                        title_provider = None
                    last_title_emit = now

            if stdin_fd in ready:
                try:
                    data = os.read(stdin_fd, 4096)
                except OSError:
                    data = b""
                if data:
                    os.write(master_fd, data)
            if master_fd in ready:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    data = b""
                if data:
                    os.write(stdout_fd, data)
                elif proc.poll() is not None:
                    break
            if proc.poll() is not None:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    data = b""
                if data:
                    os.write(stdout_fd, data)
                else:
                    break
        return int(proc.wait())
    except KeyboardInterrupt:
        with contextlib.suppress(Exception):
            os.killpg(proc.pid, signal.SIGINT)
        return int(proc.wait())
    finally:
        with contextlib.suppress(Exception):
            signal.signal(signal.SIGWINCH, old_winch)
        if old_tty is not None:
            termios.tcsetattr(stdin_fd, termios.TCSANOW, old_tty)
        with contextlib.suppress(Exception):
            os.close(master_fd)
