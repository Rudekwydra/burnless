"""Production RunnerFn implementations for MaestroSession (v1 glue).

The engine/session layer never touches subprocess; this module is the one
place that does. See _design/FABLE_READINESS_AND_VERBOSE_2026-06-09.md
Part 1.2 item 1.
"""
from __future__ import annotations

import json
import os
import subprocess

_FAILURE = {"result": "", "usage": {}, "session_id": None}


def runner_claude_json(
    cmd: list[str],
    *,
    timeout: int = 600,
    cwd: str | None = None,
) -> dict:
    """Run a `claude -p ... --output-format json` command and parse its output.

    Strips ANTHROPIC_API_KEY from the env to force Claude Code
    OAuth/subscription auth (same policy as warm_session/dispatcher).
    Returns the parsed dict exposing 'result', 'usage', 'session_id';
    on any failure returns the empty envelope (never raises).

    Matches RunnerFn = Callable[[list[str]], dict]; timeout/cwd are
    keyword-only so callers can partial them in (e.g. the maestro base
    iso-cwd, required for --resume to find the session jsonl).
    """
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=cwd, env=env,
        )
        data = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError,
            json.JSONDecodeError, ValueError):
        return dict(_FAILURE)
    if not isinstance(data, dict):
        return dict(_FAILURE)
    data.setdefault("result", "")
    data.setdefault("usage", {})
    data.setdefault("session_id", None)
    if not isinstance(data.get("usage"), dict):
        data["usage"] = {}
    return data
