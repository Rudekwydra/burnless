#!/usr/bin/env bash
# Burnless Claude Code SessionStart hook.
# Startup-only seed injection. Fail-open on all errors.
set -uo pipefail

INPUT="$(cat)" 2>/dev/null || exit 0
PYTHON_BIN="${PYTHON_BIN:-python3}"
POINTER_FILE="${BURNLESS_STATE_DIR:-$HOME/.burnless/state}/pending_seed.md"
BB="$(command -v burnless || echo "$HOME/.local/bin/burnless")"

INPUT_JSON="$INPUT" POINTER_FILE="$POINTER_FILE" BB="$BB" "$PYTHON_BIN" - <<'PY'
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _payload() -> dict:
    try:
        data = json.loads(os.environ.get("INPUT_JSON", "{}"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _burnless_cmd(*args: str) -> list[str]:
    bb = os.environ.get("BB") or "burnless"
    return [bb, *args]


def _resolve_root(cwd: str, transcript: str | None = None) -> str | None:
    proc = subprocess.run(
        _burnless_cmd("epoch", "resolve-root", "--cwd", cwd, "--workspace", os.environ.get("BURNLESS_WORKSPACE_ROOT") or os.environ.get("BURNLESS_WORKSPACE") or f"{Path.home()}/antigravity", *(("--transcript", transcript) if transcript else ())),
        capture_output=True,
        text=True,
        check=False,
    )
    root = (proc.stdout or "").strip()
    return root or None


def _log_hook_error(root: str, sid: str | None, pid: str | None, source: str, transcript: str | None, label: str, message: str) -> None:
    if not message.strip():
        return
    cmd = _burnless_cmd("epoch", "hook-error", "--root", root, "--hook", label, "--host", "claude")
    if sid:
        cmd += ["--host-session-id", sid]
    if pid:
        cmd += ["--process-instance-id", pid]
    if source:
        cmd += ["--source", source]
    if transcript:
        cmd += ["--transcript", transcript]
    subprocess.run(cmd, input=message, text=True, capture_output=True, check=False)


def _log_pilot_event(root: str, sid: str | None, pid: str | None, source: str, cwd: str | None, transcript: str | None) -> None:
    run_id = os.environ.get("BURNLESS_PILOT_RUN_ID")
    if not run_id:
        return
    payload = {
        "session_id": sid,
        "process_instance_id": pid,
        "source": source,
        "cwd": cwd,
        "transcript_path": transcript,
    }
    cmd = _burnless_cmd(
        "pilot-event",
        "--root",
        root,
        "--run-id",
        run_id,
        "--event",
        "session_start",
        "--host",
        "claude",
        "--source",
        source or "startup",
    )
    if sid:
        cmd += ["--host-session-id", sid]
    if pid:
        cmd += ["--process-instance-id", pid]
    if cwd:
        cmd += ["--cwd", cwd]
    if transcript:
        cmd += ["--transcript", transcript]
    subprocess.run(cmd, input=json.dumps(payload, ensure_ascii=False), text=True, capture_output=True, check=False)


payload = _payload()
source = str(payload.get("source") or "").strip().lower()
if source == "clear":
    sys.exit(0)

sid = str(payload.get("session_id") or "").strip() or None
cwd = str(payload.get("cwd") or "").strip() or None
pid = str(payload.get("process_instance_id") or "").strip() or None
transcript = str(payload.get("transcript_path") or "").strip() or None

if not cwd:
    sys.exit(0)

root = _resolve_root(cwd, transcript)
if not root:
    sys.exit(0)

_log_pilot_event(root, sid, pid, source or "startup", cwd, transcript)

pointer_file = Path(os.environ.get("POINTER_FILE", ""))
if pointer_file.exists():
    try:
        mtime = pointer_file.stat().st_mtime
        if time.time() - mtime > 86400:
            try:
                pointer_file.unlink()
            except OSError:
                pass
        else:
            raw = pointer_file.read_text(encoding="utf-8").strip()
            if raw:
                lines = raw.splitlines()
                target = None
                marker_prefix = "<!-- burnless-seed-target: "
                marker_suffix = " -->"
                if lines and lines[0].startswith(marker_prefix) and lines[0].endswith(marker_suffix):
                    target = lines[0][len(marker_prefix):-len(marker_suffix)].strip()
                    content = "\n".join(lines[1:]).strip()
                else:
                    content = raw
                if target is not None:
                    matched = False
                    try:
                        matched = Path(cwd).resolve().is_relative_to(Path(target).resolve())
                    except Exception:
                        matched = (cwd == target)
                    if not matched:
                        # Seed belongs to another project: leave the pointer
                        # for its owner and FALL THROUGH to the startup
                        # restore below — never silence this session's memory.
                        content = ""
                if content:
                    seed_msg = "[BURNLESS SEED] sessao iniciada leve a partir da capsule rolante.\n\n"
                    final_content = seed_msg + content
                    if len(final_content) > 4000:
                        final_content = final_content[:3950] + "\n…[truncated]"
                    print(json.dumps({
                        "hookSpecificOutput": {
                            "hookEventName": "SessionStart",
                            "additionalContext": final_content,
                        }
                    }, ensure_ascii=False))
                    try:
                        pointer_file.unlink()
                    except OSError:
                        pass
                    # Memoria eterna (pilot respawn): bootstrap this session's
                    # checkpoint from the latest project checkpoint so the
                    # living doc evolves across rollovers.
                    if sid:
                        try:
                            subprocess.run(
                                _burnless_cmd(
                                    "epoch", "inherit",
                                    "--root", root,
                                    "--host", "claude",
                                    "--new-session-id", sid,
                                    "--process-instance-id", pid or sid,
                                ),
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                        except Exception:
                            pass
                    sys.exit(0)
    except Exception:
        pass

if source not in {"", "startup", "direct"}:
    sys.exit(0)

cmd = _burnless_cmd(
    "epoch",
    "restore",
    "--root",
    root,
    "--host",
    "claude",
    "--process-instance-id",
    pid or sid or "",
    "--new-session-id",
    sid or "",
    "--source",
    "startup",
    "--budget-tokens",
    "1200",
)
proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
rest = (proc.stdout or "").strip()
if not rest:
    sys.exit(0)
print(rest)
PY
