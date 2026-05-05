from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .agents import AgentError, resolve_command

_SPINNER_FRAMES = ["|", "/", "-", "\\"]

_PHASE_WORDS: dict[str, set[str]] = {
    "lendo":       {"reading", "read", "lendo"},
    "editando":    {"writing", "write", "written", "updated", "edited", "applying", "applied", "editando"},
    "testando":    {"running", "tests", "test", "passed", "failed", "testando"},
    "auditando":   {"audit", "auditando"},
    "compactando": {"compress", "capsule", "compactando", "final json"},
}

# Maps internal Portuguese phase labels to the English labels shown in the UI.
_EN_LABELS: dict[str, str] = {
    "pensando":    "thinking",
    "lendo":       "reading",
    "editando":    "writing",
    "testando":    "testing",
    "auditando":   "auditing",
    "compactando": "compressing",
}


def _detect_phase(event: str | None) -> str:
    """Map a panel event string to a short Portuguese phase label."""
    if not event:
        return "pensando"
    low = event.lower()
    for phase, words in _PHASE_WORDS.items():
        for w in words:
            # Use word-boundary check for short words to avoid false substring matches
            # (e.g., "read" in "ready", "run" in "running" would be fine but "read" in "ready" is not).
            if len(w) <= 4:
                if re.search(rf"\b{re.escape(w)}\b", low):
                    return phase
            elif w in low:
                return phase
    return "pensando"


class _MinimalSpinner:
    """Single-line carriage-return spinner for minimal progress mode."""

    def __init__(self, *, delegation_id: str, tier: str) -> None:
        self._did = delegation_id
        self._tier = tier
        self._phase = "pensando"
        self._last_event = "starting"
        self._frame_i = 0
        self._enabled = sys.stdout.isatty()

    def start(self) -> bool:
        if not self._enabled:
            print(f"Running {self._did} ({self._tier})...", flush=True)
            return False
        self._render(idle_s=0.0)
        return True

    def stop(self) -> None:
        if self._enabled:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()

    def emit(self, event: str, elapsed_s: float) -> None:
        phase = _detect_phase(event)
        if phase != self._phase:
            self._phase = phase
        self._last_event = event
        self._frame_i += 1
        self._render(idle_s=0.0)

    def refresh(self, elapsed_s: float, *, idle_s: float = 0.0) -> None:
        self._frame_i += 1
        self._render(idle_s=idle_s)

    def final(self, *, elapsed_s: float, **_) -> None:
        self.stop()

    def _render(self, *, idle_s: float) -> None:
        if not self._enabled:
            return
        frame = _SPINNER_FRAMES[self._frame_i % len(_SPINNER_FRAMES)]
        label = _EN_LABELS.get(self._phase, self._phase)
        idle = f" · idle {_format_idle(idle_s)}" if idle_s >= 2.0 else ""
        sys.stdout.write(f"\r{frame} {self._did} {label}{idle}...   ")
        sys.stdout.flush()


@dataclass
class RunResult:
    agent: str | None
    command: list[str]
    stdout: str
    stderr: str
    returncode: int
    started_at: str
    ended_at: str
    duration_s: float
    interrupted: bool = False
    stale: bool = False

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": self.duration_s,
            "interrupted": self.interrupted,
            "stale": self.stale,
        }


def run_with_live_panel(
    *,
    delegation_id: str,
    tier: str,
    agent_cfg: dict,
    prompt: str,
    log_path: Path,
    mode: str = "watch",
    burnless_tokens: int = 0,
    timeout: int = 600,
    stale_timeout: int = 0,
    cwd: Path | None = None,
    tail_lines: int = 20,
    refresh_rate: float = 0.5,
    phase_sink: Callable[[str], None] | None = None,
) -> RunResult:
    """Run an agent while saving output and showing mode-specific progress."""
    command = resolve_command(agent_cfg)
    if shutil.which(command[0]) is None:
        raise AgentError(
            f"agent binary not found in PATH: {command[0]} (configured for {agent_cfg.get('name')})"
        )
    if mode not in {"plain", "watch", "quiet", "full", "minimal", "brief"}:
        mode = "plain"

    started = datetime.now(timezone.utc)
    start_mono = time.monotonic()
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    recent: deque[str] = deque(maxlen=tail_lines)
    event_filter = _PanelEventFilter()
    events: queue.Queue[tuple[str, str | None]] = queue.Queue()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as log:
        log.write(
            f"# agent: {agent_cfg.get('name')}\n"
            f"# command: {' '.join(command)}\n"
            f"# delegation: {delegation_id}\n"
            f"# started_at: {started.isoformat()}\n\n"
            "--- STREAM ---\n"
        )
        log.flush()

        worker_env = os.environ.copy()
        worker_env["BURNLESS_WORKER"] = "1"
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(cwd) if cwd else None,
            env=worker_env,
        )
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()

        threads = [
            threading.Thread(target=_pump, args=(proc.stdout, "stdout", events), daemon=True),
            threading.Thread(target=_pump, args=(proc.stderr, "stderr", events), daemon=True),
        ]
        for thread in threads:
            thread.start()

        interrupted = False
        stale_worker = False
        last_useful_mono = start_mono
        last_render = start_mono
        _last_sink_phase = "thinking"
        renderer = _WatchRenderer(
            enabled=mode in {"watch", "brief"},
            delegation_id=delegation_id,
            tier=tier,
            agent=agent_cfg.get("name") or "agent",
            log_path=log_path,
            burnless_tokens=burnless_tokens,
            tail_lines=tail_lines,
            transient=(mode == "brief"),
        )
        minimal_spinner: _MinimalSpinner | None = None
        if mode == "minimal":
            minimal_spinner = _MinimalSpinner(delegation_id=delegation_id, tier=tier)
        try:
            if mode in {"watch", "brief"} and not renderer.start():
                mode = "plain"
            elif mode == "minimal":
                assert minimal_spinner is not None
                if not minimal_spinner.start():
                    mode = "plain"
            if mode == "plain":
                print(f"Running {delegation_id} with {tier}/{agent_cfg.get('name') or 'agent'}...", flush=True)
            while True:
                now = time.monotonic()
                try:
                    stream, line = events.get(timeout=0.1)
                except queue.Empty:
                    stream, line = "", None

                if line is not None:
                    if stream == "stdout":
                        stdout_parts.append(line)
                    else:
                        stderr_parts.append(line)
                    clean = line.rstrip("\n")
                    if clean:
                        last_useful_mono = now
                        panel_event = event_filter.feed(clean)
                        if panel_event:
                            recent.append(panel_event)
                            if minimal_spinner is not None:
                                minimal_spinner.emit(panel_event, now - start_mono)
                            else:
                                renderer.emit(panel_event, now - start_mono)
                            if phase_sink is not None:
                                _new_phase = _EN_LABELS.get(_detect_phase(panel_event), "thinking")
                                if _new_phase != _last_sink_phase:
                                    phase_sink(_new_phase)
                                    _last_sink_phase = _new_phase
                    log.write(f"[{stream}] {line}")
                    log.flush()
                    if mode == "full":
                        target = sys.stdout if stream == "stdout" else sys.stderr
                        target.write(line)
                        target.flush()

                if proc.poll() is not None and events.empty():
                    break
                if time.monotonic() - start_mono > timeout:
                    interrupted = True
                    _stop_process(proc)
                    recent.append(f"Timed out after {timeout}s.")
                    break
                if stale_timeout > 0 and time.monotonic() - last_useful_mono > stale_timeout:
                    stale_worker = True
                    interrupted = True
                    _stop_process(proc)
                    recent.append(f"Stale worker: no output for {stale_timeout}s, process killed.")
                    break
                if mode in {"watch", "brief"} and now - last_render >= refresh_rate:
                    if not renderer.refresh(
                        elapsed_s=now - start_mono,
                        recent=list(recent),
                        idle_s=now - last_useful_mono,
                    ):
                        mode = "plain"
                    last_render = now
                elif mode == "minimal" and now - last_render >= 0.1:
                    if minimal_spinner is not None:
                        minimal_spinner.refresh(now - start_mono, idle_s=now - last_useful_mono)
                    last_render = now
                elif mode == "quiet" and now - last_render >= 10:
                    _render_quiet(
                        delegation_id=delegation_id,
                        tier=tier,
                        agent=agent_cfg.get("name") or "agent",
                        elapsed_s=now - start_mono,
                        log_path=log_path,
                    )
                    last_render = now
        except KeyboardInterrupt:
            if mode in {"watch", "brief"}:
                renderer.stop()
                sys.stdout.write("\n")
            elif mode == "minimal" and minimal_spinner is not None:
                minimal_spinner.stop()
            answer = input("Stop worker safely? [Y/n] ").strip().lower()
            if answer in {"", "y", "yes", "s", "sim"}:
                interrupted = True
                _stop_process(proc)
                recent.append("Worker stopped by user.")
            else:
                return _continue_after_interrupt(
                    proc=proc,
                    events=events,
                    log=log,
                    stdout_parts=stdout_parts,
                    stderr_parts=stderr_parts,
                    recent=recent,
                    delegation_id=delegation_id,
                    tier=tier,
                    agent_cfg=agent_cfg,
                    log_path=log_path,
                    mode=mode,
                    burnless_tokens=burnless_tokens,
                    start_mono=start_mono,
                    started=started,
                    command=command,
                    refresh_rate=refresh_rate,
                    event_filter=event_filter,
                    phase_sink=phase_sink,
                )
        except Exception:
            if mode in {"watch", "brief"}:
                renderer.stop()
            elif mode == "minimal" and minimal_spinner is not None:
                minimal_spinner.stop()
            raise

        for thread in threads:
            thread.join(timeout=0.2)

        ended = datetime.now(timezone.utc)
        returncode = proc.returncode
        if interrupted and returncode is None:
            returncode = 130
        elif returncode is None:
            returncode = proc.wait(timeout=1)
        log.write(
            "\n--- END ---\n"
            f"# returncode: {returncode}\n"
            f"# duration_s: {(ended - started).total_seconds()}\n"
            f"# ended_at: {ended.isoformat()}\n"
        )
        log.flush()

    if mode in {"watch", "brief"}:
        renderer.final(
            elapsed_s=time.monotonic() - start_mono,
            recent=list(recent),
            status="stopped" if interrupted else "finished",
        )
    elif mode == "minimal" and minimal_spinner is not None:
        minimal_spinner.final(elapsed_s=time.monotonic() - start_mono)
    elif mode == "quiet":
        pass

    return RunResult(
        agent=agent_cfg.get("name"),
        command=command,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        returncode=returncode,
        started_at=started.isoformat(),
        ended_at=ended.isoformat(),
        duration_s=(ended - started).total_seconds(),
        interrupted=interrupted,
        stale=stale_worker,
    )


def _pump(pipe, stream: str, events: queue.Queue) -> None:
    if pipe is None:
        return
    for line in pipe:
        events.put((stream, line))
    pipe.close()


def _continue_after_interrupt(**kwargs) -> RunResult:
    # Re-enter the same loop by tail-calling the process wait path would be more
    # complex than useful here; keep the worker alive and collect the remainder.
    proc = kwargs["proc"]
    events = kwargs["events"]
    log = kwargs["log"]
    stdout_parts = kwargs["stdout_parts"]
    stderr_parts = kwargs["stderr_parts"]
    recent = kwargs["recent"]
    mode = kwargs["mode"]
    event_filter = kwargs["event_filter"]
    start_mono = kwargs["start_mono"]
    started = kwargs["started"]
    command = kwargs["command"]
    agent_cfg = kwargs["agent_cfg"]
    delegation_id = kwargs["delegation_id"]
    tier = kwargs["tier"]
    log_path = kwargs["log_path"]
    burnless_tokens = kwargs["burnless_tokens"]
    refresh_rate = kwargs["refresh_rate"]
    phase_sink: Callable[[str], None] | None = kwargs.get("phase_sink")
    last_sink_phase = "thinking"
    last_render = start_mono
    last_useful_mono = start_mono
    renderer = _WatchRenderer(
        enabled=mode in {"watch", "brief"},
        delegation_id=delegation_id,
        tier=tier,
        agent=agent_cfg.get("name") or "agent",
        log_path=log_path,
        burnless_tokens=burnless_tokens,
        tail_lines=recent.maxlen or 20,
        transient=(mode == "brief"),
    )
    minimal_spinner2: _MinimalSpinner | None = None
    if mode == "minimal":
        minimal_spinner2 = _MinimalSpinner(delegation_id=delegation_id, tier=tier)
    if mode in {"watch", "brief"} and not renderer.start():
        mode = "plain"
    elif mode == "minimal" and minimal_spinner2 is not None:
        if not minimal_spinner2.start():
            mode = "plain"
    while True:
        now = time.monotonic()
        try:
            stream, line = events.get(timeout=0.1)
        except queue.Empty:
            stream, line = "", None
        if line is not None:
            if stream == "stdout":
                stdout_parts.append(line)
            else:
                stderr_parts.append(line)
            clean = line.rstrip("\n")
            if clean:
                last_useful_mono = now
                panel_event = event_filter.feed(clean)
                if panel_event:
                    recent.append(panel_event)
                    if minimal_spinner2 is not None:
                        minimal_spinner2.emit(panel_event, now - start_mono)
                    else:
                        renderer.emit(panel_event, now - start_mono)
                    if phase_sink is not None:
                        new_phase = _EN_LABELS.get(_detect_phase(panel_event), "thinking")
                        if new_phase != last_sink_phase:
                            phase_sink(new_phase)
                            last_sink_phase = new_phase
            log.write(f"[{stream}] {line}")
            log.flush()
            if mode == "full":
                target = sys.stdout if stream == "stdout" else sys.stderr
                target.write(line)
                target.flush()
        if proc.poll() is not None and events.empty():
            break
        if mode in {"watch", "brief"} and now - last_render >= refresh_rate:
            if not renderer.refresh(elapsed_s=now - start_mono, recent=list(recent), idle_s=now - last_useful_mono):
                mode = "plain"
            last_render = now
        elif mode == "minimal" and now - last_render >= 0.1:
            if minimal_spinner2 is not None:
                minimal_spinner2.refresh(now - start_mono, idle_s=now - last_useful_mono)
            last_render = now
        elif mode == "quiet" and now - last_render >= 10:
            _render_quiet(
                delegation_id=delegation_id,
                tier=tier,
                agent=agent_cfg.get("name") or "agent",
                elapsed_s=now - start_mono,
                log_path=log_path,
            )
            last_render = now
    ended = datetime.now(timezone.utc)
    log.write(
        "\n--- END ---\n"
        f"# returncode: {proc.returncode if proc.returncode is not None else 0}\n"
        f"# duration_s: {(ended - started).total_seconds()}\n"
        f"# ended_at: {ended.isoformat()}\n"
    )
    log.flush()
    if mode in {"watch", "brief"}:
        renderer.final(
            elapsed_s=time.monotonic() - start_mono,
            recent=list(recent),
            status="finished",
        )
    elif mode == "minimal" and minimal_spinner2 is not None:
        minimal_spinner2.final(elapsed_s=time.monotonic() - start_mono)
    return RunResult(
        agent=agent_cfg.get("name"),
        command=command,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        returncode=proc.returncode if proc.returncode is not None else 0,
        started_at=started.isoformat(),
        ended_at=ended.isoformat(),
        duration_s=(ended - started).total_seconds(),
        interrupted=False,
    )


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


class _WatchRenderer:
    def __init__(
        self,
        *,
        enabled: bool,
        delegation_id: str,
        tier: str,
        agent: str,
        log_path: Path,
        burnless_tokens: int,
        tail_lines: int,
        transient: bool = False,
    ) -> None:
        self.enabled = enabled
        self.delegation_id = delegation_id
        self.tier = tier
        self.agent = agent
        self.log_path = log_path
        self.burnless_tokens = burnless_tokens
        self.tail_lines = tail_lines
        self._transient = transient
        self._live = None
        self._using_rich = False
        self._phase = "thinking"

    def start(self) -> bool:
        if not self.enabled:
            return False
        if not sys.stdout.isatty():
            return False
        try:
            from rich.console import Console
            from rich.live import Live
        except Exception:
            return False
        try:
            console = Console()
            if not console.is_terminal:
                return False
            self._live = Live(
                self._rich_renderable(0, [], "running", idle_s=0.0),
                console=console,
                refresh_per_second=4,
                transient=self._transient,
            )
            self._live.start()
        except Exception:
            self._live = None
            self._using_rich = False
            return False
        self._using_rich = True
        return True

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def refresh(self, *, elapsed_s: float, recent: list[str], status: str = "running", idle_s: float = 0.0) -> bool:
        if not self.enabled:
            return False
        if self._using_rich and self._live is not None:
            try:
                self._live.update(self._rich_renderable(elapsed_s, recent, status, idle_s=idle_s))
                return True
            except Exception:
                self.stop()
                self._using_rich = False
                return False
        return False

    def emit(self, event: str, elapsed_s: float) -> None:
        if not self.enabled:
            return
        self._phase = _EN_LABELS.get(_detect_phase(event), "thinking")

    def final(self, *, elapsed_s: float, recent: list[str], status: str) -> None:
        if not self.enabled:
            return
        if self._using_rich and self._live is not None:
            try:
                self._live.update(self._rich_renderable(elapsed_s, recent, status, idle_s=0.0))
            except Exception:
                pass
            finally:
                self.stop()
                sys.stdout.write("\n")

    def _rich_renderable(self, elapsed_s: float, recent: list[str], status: str, *, idle_s: float = 0.0):
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.text import Text

        body = recent[-self.tail_lines:] or ["Worker is starting..."]
        worker = Text("\n".join(body), no_wrap=False)
        idle = f" · idle {_format_idle(idle_s)}" if idle_s >= 2.0 else ""
        heartbeat = f"heartbeat: {self._phase}{idle}"
        return Group(
            "🔥 Burnless",
            "",
            f"{self.delegation_id} → {self.tier}/{self.agent}",
            f"status: {status}",
            heartbeat,
            f"elapsed: {_format_elapsed(elapsed_s)}",
            f"log: {_display_path(self.log_path)}",
            "",
            Panel(
                worker,
                title=f"Worker: {self.agent}",
                title_align="left",
                box=box.ROUNDED,
                expand=True,
            ),
            "",
            f"{self.burnless_tokens:,} burnless tokens",
        )


class _PanelEventFilter:
    _HIDDEN_HEADINGS = {
        "## task",
        "## constraints",
        "## success criteria",
        "## required final output",
        "## required final output (last lines of stdout)",
    }
    _USEFUL_PATTERNS = (
        (re.compile(r"\b(reading|read)\s+([\w./ -]+\.[\w-]+)", re.I), "Reading {file}"),
        (re.compile(r"\b(writing|write)\s+([\w./ -]+\.[\w-]+)", re.I), "Writing {file}"),
        (re.compile(r"\b(updated|modified|edited)\s+([\w./ -]+\.[\w-]+)", re.I), "Updated {file}"),
        (re.compile(r"\b(applying|applied)\s+patch\b", re.I), "Applying patch"),
        (re.compile(r"\b(running|run)\s+(validation|tests?|command)\b", re.I), "Running {what}"),
        (re.compile(r"\b(command|tests?)\s+(succeeded|passed)\b", re.I), "{what} passed"),
        (re.compile(r"\b(command|tests?)\s+(failed)\b", re.I), "{what} failed"),
    )

    def __init__(self) -> None:
        self._skip_prompt_section = False
        self._last: str | None = None

    def feed(self, line: str) -> str | None:
        stripped = _strip_ansi(line).strip()
        if not stripped:
            return None
        lowered = stripped.lower()
        if lowered in self._HIDDEN_HEADINGS:
            self._skip_prompt_section = True
            return None
        if stripped.startswith("## "):
            self._skip_prompt_section = lowered in self._HIDDEN_HEADINGS
            return None
        if self._skip_prompt_section:
            return None
        if stripped.startswith(("{", "}", '"')) or stripped in {"```", "```json"}:
            return None
        if "final json" in lowered:
            return self._dedupe("Waiting for final JSON...")
        for pattern, template in self._USEFUL_PATTERNS:
            match = pattern.search(stripped)
            if not match:
                continue
            event = self._format_match(template, match)
            return self._dedupe(event)
        if stripped.startswith(("Reading ", "Writing ", "Updated ", "Applying ", "Running ")):
            return self._dedupe(stripped[:120])
        return None

    def _format_match(self, template: str, match: re.Match[str]) -> str:
        groups = match.groups()
        file_value = groups[-1].strip("`'\" ") if groups else ""
        what = groups[-2] if len(groups) > 1 else groups[0] if groups else "command"
        return template.format(file=file_value, what=what.capitalize())

    def _dedupe(self, event: str) -> str | None:
        if event == self._last:
            return None
        self._last = event
        return event


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _render_quiet(
    *,
    delegation_id: str,
    tier: str,
    agent: str,
    elapsed_s: float,
    log_path: Path,
) -> None:
    print(
        f"[{_format_elapsed(elapsed_s)}] {delegation_id} running with {tier}/{agent} "
        f"— log: {_display_path(log_path)}",
        flush=True,
    )


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_idle(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m, remainder = divmod(s, 60)
    return f"{m}m {remainder}s"
