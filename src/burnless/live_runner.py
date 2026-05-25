from __future__ import annotations

import json
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
from . import liveness as liveness_mod

_OVERFLOW_PATTERNS = (
    re.compile(r"prompt is too long", re.IGNORECASE),
    re.compile(r"context_length_exceeded", re.IGNORECASE),
    re.compile(r"max_tokens", re.IGNORECASE),
)
_OVERFLOW_TIER_ORDER = ("bronze", "silver", "gold", "diamond")
_OVERFLOW_HISTORY_TURNS = 5
_OVERFLOW_MAX_ATTEMPTS = 3




def _translate_stream_json(line: str, text_acc: list[str], session_holder: list[str] | None = None) -> str | None:
    """If `line` is a claude stream-json NDJSON event, return a one-line human
    summary and append any consolidated assistant text to `text_acc`. Returns
    `None` when the line isn't a recognized event (caller should treat as raw).
    """
    s = line.strip()
    if not s.startswith("{"):
        return None
    try:
        ev = json.loads(s)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(ev, dict):
        return None
    et = ev.get("type")
    if et == "assistant":
        msg = ev.get("message") or {}
        blocks = msg.get("content") or []
        labels: list[str] = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                txt = b.get("text") or ""
                if not txt.strip():
                    continue
                text_acc.append(txt)
                # Full delta — shell consolidates consecutive [text] lines.
                labels.append(f"[text] {txt}")
            elif bt == "thinking":
                th = b.get("thinking") or ""
                if th.strip():
                    # Full delta — shell consolidates consecutive [thinking] lines.
                    labels.append(f"[thinking] {th}")
            elif bt == "tool_use":
                name = b.get("name") or "tool"
                inp = b.get("input") or {}
                preview = json.dumps(inp, ensure_ascii=False)[:160] if inp else ""
                labels.append(f"[tool] {name}({preview})")
            elif bt == "tool_result":
                content = b.get("content")
                if isinstance(content, list):
                    snippet = " ".join(
                        (c.get("text") or "")[:120]
                        for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    )
                else:
                    snippet = str(content or "")[:120]
                labels.append(f"[tool_result] {snippet}".rstrip())
        # Cumulative usage — emitted as a separate log line so the shell can
        # surface ↑input/↓output counts on the panel border in real time.
        usage = msg.get("usage") or {}
        if usage:
            in_t = int(usage.get("input_tokens") or 0)
            out_t = int(usage.get("output_tokens") or 0)
            cr = int(usage.get("cache_read_input_tokens") or 0)
            cw = int(usage.get("cache_creation_input_tokens") or 0)
            if in_t or out_t or cr or cw:
                labels.append(f"[usage] in={in_t} out={out_t} cache_read={cr} cache_write={cw}")
        return "\n".join(labels) if labels else None
    if et == "result":
        # Final result event — append the consolidated answer if not already
        # captured via the assistant text deltas.
        final = ev.get("result")
        if isinstance(final, str) and final and (not text_acc or not "".join(text_acc).endswith(final)):
            text_acc.append(final)
        # Capture session_id so the caller can write per-tier resume state.
        sid = ev.get("session_id")
        if isinstance(sid, str) and sid and session_holder is not None:
            session_holder.clear()
            session_holder.append(sid)
        rc = ev.get("is_error")
        return "[done]" + (" (error)" if rc else "")
    if et == "system":
        sub = ev.get("subtype") or ev.get("type")
        return f"[system] {sub}" if sub else None
    return None

_SPINNER_FRAMES = ["|", "/", "-", "\\"]

_PHASE_WORDS: dict[str, set[str]] = {
    "lendo":       {"reading", "read", "lendo"},
    "editando":    {"writing", "write", "written", "updated", "edited", "applying", "applied", "editando"},
    "testando":    {"running", "tests", "test", "passed", "failed", "testando"},
    "compactando": {"compress", "capsule", "compactando", "final json"},
}

# Maps internal Portuguese phase labels to the English labels shown in the UI.
_EN_LABELS: dict[str, str] = {
    "pensando":    "thinking",
    "lendo":       "reading",
    "editando":    "writing",
    "testando":    "testing",
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


def _emit_suspect(
    burnless_root: Path | None,
    delegation_id: str | None,
    tool_elapsed_s: int,
    tool_cmd_preview: str,
    io_idle_s: int | None = None,
) -> None:
    """Emit a suspect alert: stderr for real-time visibility, JSONL inbox for forensic / future Maestro daemon."""
    did = delegation_id or "d???"
    cmd_short = tool_cmd_preview[:80] + ("…" if len(tool_cmd_preview) > 80 else "")
    if io_idle_s is not None:
        msg = f"[suspect] {did}: tool running {tool_elapsed_s}s (io idle {io_idle_s}s), cmd: {cmd_short}"
    else:
        msg = f"[suspect] {did}: tool running {tool_elapsed_s}s, cmd: {cmd_short}"
    print(msg, file=sys.stderr, flush=True)
    if burnless_root:
        inbox = Path(burnless_root) / "suspect.jsonl"
        try:
            inbox.parent.mkdir(parents=True, exist_ok=True)
            with inbox.open("a", encoding="utf-8") as f:
                row = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "did": did,
                    "tool_elapsed_s": tool_elapsed_s,
                    "tool_cmd_preview": cmd_short,
                }
                if io_idle_s is not None:
                    row["io_idle_s"] = io_idle_s
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass
    try:
        from . import liveness as _live
        _live.emit(burnless_root, delegation_id, "suspect",
                   tool_elapsed_s=tool_elapsed_s,
                   tool_cmd_preview=tool_cmd_preview,
                   io_idle_s=io_idle_s)
    except Exception:
        pass


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
    tool_suspect_interval_s: int = 60,
    tool_hard_max_s: int = 1800,
    cwd: Path | None = None,
    tail_lines: int = 20,
    refresh_rate: float = 0.5,
    phase_sink: Callable[[str], None] | None = None,
    append_log: bool = False,
    append_label: str | None = None,
    liveness_mode: str = "time",
    warm_codex_brief: str = "",
    warm_codex_flags: list[str] | None = None,
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
    consolidated_text: list[str] = []  # Built from assistant-text events when stream-json mode is active.
    saw_stream_json = False
    session_holder: list[str] = []  # Captures session_id from the result event for per-tier resume.
    recent: deque[str] = deque(maxlen=tail_lines)
    event_filter = _PanelEventFilter()
    events: queue.Queue[tuple[str, str | None]] = queue.Queue()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Worker session strategy (gold standard validated 2026-05-22):
    #   1. If a warm session exists for this project → fork off it
    #      (--resume <warm> --fork-session). The warm carries the project
    #      brief (paths, write rules) as cached prefix.
    #   2. Otherwise fall back to per-tier resume legacy behavior, but
    #      always pair with --fork-session to prevent cross-task
    #      contamination from the prior delegation.
    burnless_root = log_path.parent.parent if log_path.parent.name == "logs" else log_path.parent
    try:
        from . import liveness as _live
        _live.init_run_dir(burnless_root, delegation_id)
        _live.emit(burnless_root, delegation_id, "start",
                   agent=str(agent_cfg.get("name", "")),
                   tier=tier,
                   cwd=str(cwd) if cwd else "",
                   command_head=command[0] if command else "")
    except Exception:
        pass
    fork_uuid: str | None = None
    if "--resume" not in command:
        from .agents import _detect_provider_from_parts, _extract_model_from_parts
        provider = _detect_provider_from_parts(list(command))
        if provider is not None:
            model = _extract_model_from_parts(list(command))
            if model is None:
                model = "claude-sonnet-4-6" if provider == "claude" else "gpt-5.2"
            try:
                if provider == "claude":
                    from . import warm_session as _ws
                else:
                    from . import warm_session_codex as _ws
                warm_args = _ws.fork_args(burnless_root, model)
                if not warm_args:
                    try:
                        _ws.init(burnless_root, model=model)
                        warm_args = _ws.fork_args(burnless_root, model)
                    except Exception as _e:
                        print(
                            f"[burnless] WARN: live_runner warm init failed for "
                            f"{provider}/{model} ({_e}); worker COLD.",
                            file=sys.stderr, flush=True,
                        )
                        warm_args = []
                if warm_args:
                    command = list(command) + warm_args
                    fork_uuid = warm_args[1] if len(warm_args) > 1 else None
                else:
                    print(
                        f"[burnless] WARN: no warm fork args available for "
                        f"{provider}/{model}; worker spawning COLD.",
                        file=sys.stderr, flush=True,
                    )
            except Exception as _e:
                print(
                    f"[burnless] WARN: live_runner warm module unavailable ({_e}); "
                    f"worker COLD.",
                    file=sys.stderr, flush=True,
                )

    # Bare-equivalent flags for OAuth/subscription workers — drops slash
    # commands, MCP servers, per-worker session persistence, the user-level
    # settings.json (so hooks like forgetless auto-rank don't inject contaminating
    # context into the worker via UserPromptSubmit), and dynamic per-machine
    # sections (cwd/env/git/memory) that drift between warm-init and fork and
    # cause cache_miss_reason: system_changed. Idempotent. Keeps prefix
    # byte-stable (CLI flags don't enter the cached system prompt).
    for _flag in (
        "--no-session-persistence",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections",
    ):
        if _flag not in command:
            command = list(command) + [_flag]
    if "--setting-sources" not in command:
        command = list(command) + ["--setting-sources", "project,local"]

    # Inject --permission-mode bypassPermissions for `claude` workers so
    # tool calls never trigger an interactive approval prompt (stdin is
    # already closed after writing the task — any prompt would freeze the
    # worker indefinitely).
    if command and command[0] in ("claude", "claude-cli") and "--permission-mode" not in command:
        command = list(command) + ["--permission-mode", "bypassPermissions"]

    if warm_codex_brief:
        prompt = warm_codex_brief + prompt

    if warm_codex_flags:
        new_cmd: list[str] = []
        inserted = False
        for _arg in command:
            new_cmd.append(_arg)
            if _arg == "exec" and not inserted:
                new_cmd.extend(warm_codex_flags)
                inserted = True
        if not inserted:
            new_cmd.extend(warm_codex_flags)
        command = new_cmd

    with log_path.open("a" if append_log else "w", encoding="utf-8") as log:
        if append_log:
            header = append_label or "PROVIDER FALLBACK ATTEMPT"
            log.write(f"\n\n--- {header} @ {datetime.now(timezone.utc).isoformat()} ---\n")
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
        # Let local tool guards recognize this subprocess as a Burnless worker
        # so delegations aren't blocked by "no direct edits" policies.
        if delegation_id:
            worker_env["BURNLESS_TASK_ID"] = str(delegation_id)
        # Force `claude -p` (and any tier subprocess) to authenticate via Claude
        # Code OAuth/subscription instead of falling through to API billing. The
        # in-process SDK paths still read the key directly from ANTHROPIC_ENV_PATHS.
        worker_env.pop("ANTHROPIC_API_KEY", None)
        # Run the worker in an isolated CWD outside any project tree so claude
        # code's CLAUDE.md auto-discovery walk-up finds nothing project-specific.
        # The warm session's jsonl was saved under the same iso-cwd path, so
        # --resume keeps working. Worker addresses project files via absolute
        # paths (spec convention).
        worker_cwd = str(cwd) if cwd else None
        if fork_uuid:
            try:
                from . import warm_session as _ws
                iso = _ws.worker_cwd(burnless_root)
                if iso:
                    worker_cwd = iso
            except Exception:
                pass
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=worker_cwd,
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
        in_tool_execution = False
        tool_start_mono = 0.0
        tool_cmd_preview = ""
        last_suspect_alert_mono = 0.0
        io_baseline: dict = {}
        last_io_change_mono = 0.0
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
                    # Translate claude stream-json events to human lines for log + panel.
                    # Falls through to raw mode when the worker isn't streaming NDJSON
                    # (so legacy text-mode configs keep working).
                    translated: str | None = None
                    if clean and stream == "stdout":
                        translated = _translate_stream_json(clean, consolidated_text, session_holder)
                        if translated is not None:
                            saw_stream_json = True
                    display_line = translated if translated is not None else clean
                    if display_line:
                        last_useful_mono = now
                        if display_line.startswith("[tool] "):
                            in_tool_execution = True
                            tool_start_mono = now
                            tool_cmd_preview = display_line[7:107]
                            last_suspect_alert_mono = now
                            try:
                                from . import liveness as _live
                                _live.emit(burnless_root, delegation_id, "tool_start",
                                           preview=tool_cmd_preview)
                            except Exception:
                                pass
                            if liveness_mode == "psutil":
                                io_baseline = liveness_mod.capture_io_baseline(proc.pid)
                                last_io_change_mono = now
                        elif in_tool_execution and (
                            display_line.startswith("[tool_result]")
                            or display_line.startswith("[done]")
                            or display_line.startswith("[text]")
                        ):
                            in_tool_execution = False
                            try:
                                from . import liveness as _live
                                _live.emit(burnless_root, delegation_id, "tool_done")
                            except Exception:
                                pass
                        panel_event = event_filter.feed(display_line)
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
                    if translated is not None:
                        # Human-friendly line in the log; raw NDJSON omitted.
                        log.write(f"{translated}\n")
                    else:
                        log.write(f"[{stream}] {line}")
                    log.flush()
                    if mode == "full":
                        target = sys.stdout if stream == "stdout" else sys.stderr
                        target.write(line)
                        target.flush()

                if proc.poll() is not None and events.empty():
                    break
                if not in_tool_execution and time.monotonic() - start_mono > timeout:
                    interrupted = True
                    _stop_process(proc)
                    recent.append(f"Timed out after {timeout}s.")
                    break
                now_mono = time.monotonic()
                if in_tool_execution:
                    tool_elapsed = now_mono - tool_start_mono
                    if liveness_mode == "psutil" and io_baseline:
                        changed, io_baseline = liveness_mod.io_changed_since(proc.pid, io_baseline)
                        if changed:
                            last_io_change_mono = now_mono
                    if (tool_elapsed > tool_suspect_interval_s
                            and now_mono - last_suspect_alert_mono >= tool_suspect_interval_s):
                        last_suspect_alert_mono = now_mono
                        io_idle = None
                        if liveness_mode == "psutil":
                            io_idle = int(now_mono - last_io_change_mono)
                        _emit_suspect(
                            burnless_root=burnless_root,
                            delegation_id=delegation_id,
                            tool_elapsed_s=int(tool_elapsed),
                            tool_cmd_preview=tool_cmd_preview,
                            io_idle_s=io_idle,
                        )
                    if tool_elapsed > tool_hard_max_s:
                        stale_worker = True
                        interrupted = True
                        _stop_process(proc)
                        recent.append(
                            f"Tool hard-max exceeded: {int(tool_elapsed)}s > {tool_hard_max_s}s, process killed."
                        )
                        break
                elif stale_timeout > 0 and now_mono - last_useful_mono > stale_timeout:
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

    # When stream-json events were detected, expose the consolidated assistant
    # text as stdout so extract_result_json can find the agent's final JSON
    # block. Otherwise fall back to the raw subprocess stdout (text mode).
    final_stdout = (
        "\n".join(consolidated_text) if saw_stream_json and consolidated_text
        else "".join(stdout_parts)
    )
    try:
        from . import liveness as _live
        _exit_code = proc.returncode if proc.returncode is not None else -1
        _live.emit(burnless_root, delegation_id, "finish",
                   exit_code=_exit_code,
                   interrupted=bool(interrupted),
                   stale_worker=bool(stale_worker))
    except Exception:
        pass
    return RunResult(
        agent=agent_cfg.get("name"),
        command=command,
        stdout=final_stdout,
        stderr="".join(stderr_parts),
        returncode=returncode,
        started_at=started.isoformat(),
        ended_at=ended.isoformat(),
        duration_s=(ended - started).total_seconds(),
        interrupted=interrupted,
        stale=stale_worker,
    )


def is_context_overflow_text(text: str) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in _OVERFLOW_PATTERNS)


def is_context_overflow_result(result: RunResult | dict) -> bool:
    stdout = result.stdout if isinstance(result, RunResult) else str(result.get("stdout") or "")
    stderr = result.stderr if isinstance(result, RunResult) else str(result.get("stderr") or "")
    combined = f"{stdout}\n{stderr}"
    if not is_context_overflow_text(combined):
        return False
    try:
        from . import delegations as deleg_mod
        parsed = deleg_mod.extract_result_json(stdout)
    except Exception:
        parsed = None
    if isinstance(parsed, dict) and str(parsed.get("status") or "").upper() == "OK":
        return False
    return True


def truncate_prompt_history(prompt: str, *, keep_turns: int = _OVERFLOW_HISTORY_TURNS) -> str:
    marker = "\n[recent conversation]"
    next_marker = "\n[new message]"
    if marker not in prompt or next_marker not in prompt:
        return prompt
    prefix, rest = prompt.split(marker, 1)
    history_block, suffix = rest.split(next_marker, 1)
    history_lines = [line for line in history_block.splitlines() if line.startswith(("user: ", "assistant: "))]
    keep_lines = max(keep_turns, 0) * 2
    if keep_lines <= 0 or len(history_lines) <= keep_lines:
        return prompt
    trimmed_lines = history_lines[-keep_lines:]
    return prefix + marker + "\n" + "\n".join(trimmed_lines) + next_marker + suffix


def next_overflow_tier(current_tier: str, tier_agents: dict[str, dict] | None) -> str | None:
    if not tier_agents:
        return None
    try:
        idx = _OVERFLOW_TIER_ORDER.index(current_tier)
    except ValueError:
        return None
    for candidate in _OVERFLOW_TIER_ORDER[idx + 1:]:
        if isinstance(tier_agents.get(candidate), dict):
            return candidate
    return None


def _overflow_error_result(
    *,
    delegation_id: str,
    tier: str,
    command: list[str],
    started_at: str,
    log_path: Path,
    duration_s: float,
    last_result: RunResult,
) -> RunResult:
    ended = datetime.now(timezone.utc).isoformat()
    payload = {
        "id": delegation_id,
        "status": "ERR",
        "kind": "execution",
        "summary": "context overflow persisted after truncation and tier escalation",
        "files_touched": [],
        "validated": [],
        "evidence": [
            f"log check: {log_path} contains OVERFLOW_RETRY_1",
            f"log check: {log_path} contains OVERFLOW_RETRY_2",
        ],
        "issues": ["context_overflow_retry_exhausted", f"tier={tier}"],
        "next": "",
    }
    return RunResult(
        agent=last_result.agent,
        command=command,
        stdout=f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```",
        stderr=last_result.stderr,
        returncode=last_result.returncode,
        started_at=started_at,
        ended_at=ended,
        duration_s=duration_s,
        interrupted=last_result.interrupted,
        stale=last_result.stale,
    )


def run_with_overflow_retries(
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
    tool_suspect_interval_s: int = 60,
    tool_hard_max_s: int = 1800,
    cwd: Path | None = None,
    tail_lines: int = 20,
    refresh_rate: float = 0.5,
    phase_sink: Callable[[str], None] | None = None,
    tier_agents: dict[str, dict] | None = None,
    overflow_history_turns: int = _OVERFLOW_HISTORY_TURNS,
    max_attempts: int = _OVERFLOW_MAX_ATTEMPTS,
    liveness_mode: str = "time",
    warm_codex_brief: str = "",
    warm_codex_flags: list[str] | None = None,
) -> RunResult:
    current_tier = tier
    current_cfg = agent_cfg
    current_prompt = prompt
    truncated_prompt = prompt
    log_label: str | None = None
    saw_truncation = False
    first_started_at: str | None = None
    last_result: RunResult | None = None
    total_start = time.monotonic()

    for attempt_idx in range(max_attempts):
        result = run_with_live_panel(
            delegation_id=delegation_id,
            tier=current_tier,
            agent_cfg=current_cfg,
            prompt=current_prompt,
            log_path=log_path,
            mode=mode,
            burnless_tokens=burnless_tokens,
            timeout=timeout,
            stale_timeout=stale_timeout,
            tool_suspect_interval_s=tool_suspect_interval_s,
            tool_hard_max_s=tool_hard_max_s,
            cwd=cwd,
            tail_lines=tail_lines,
            refresh_rate=refresh_rate,
            phase_sink=phase_sink,
            append_log=attempt_idx > 0,
            append_label=log_label,
            liveness_mode=liveness_mode,
            warm_codex_brief=warm_codex_brief,
            warm_codex_flags=warm_codex_flags,
        )
        last_result = result
        first_started_at = first_started_at or result.started_at
        if not is_context_overflow_result(result):
            return result
        if attempt_idx >= max_attempts - 1:
            break
        retry_no = attempt_idx + 1
        if not saw_truncation:
            truncated_prompt = truncate_prompt_history(prompt, keep_turns=overflow_history_turns)
            current_prompt = truncated_prompt
            saw_truncation = True
            log_label = f"OVERFLOW_RETRY_{retry_no} truncate-history tier={current_tier}"
            continue
        next_tier_name = next_overflow_tier(current_tier, tier_agents)
        if not next_tier_name:
            break
        current_tier = next_tier_name
        current_cfg = tier_agents[next_tier_name]
        current_prompt = truncated_prompt
        log_label = f"OVERFLOW_RETRY_{retry_no} escalate tier={tier}->{current_tier}"

    assert last_result is not None
    return _overflow_error_result(
        delegation_id=delegation_id,
        tier=current_tier,
        command=last_result.command,
        started_at=first_started_at or last_result.started_at,
        log_path=log_path,
        duration_s=max(time.monotonic() - total_start, last_result.duration_s),
        last_result=last_result,
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
