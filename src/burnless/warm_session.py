"""Warm session pool for Burnless workers (gold standard discovered 2026-05-22).

Workers invoked via `claude -p --resume <warm_uuid> --fork-session` inherit a
cached prefix from a project-scoped warm session, eliminating ~40x of
cache_write per task compared to one-shot stateless invocation, while keeping
forks isolated (no cross-worker contamination).

State lives at .burnless/warm_session.json:
    {
      "uuid": "<uuid4>",
      "created_at": "<iso8601>",
      "last_used": "<iso8601>",
      "project_root": "<abs path>",
      "brief": "<W0 prompt sent at init>"
    }

The session jsonl itself lives where Claude Code stores it (path-derived under
~/.claude/projects/<cwd-dashes>/<uuid>.jsonl) — we only keep the UUID.

Heartbeat policy: refresh when last_used > 50 minutes ago (TTL is 1h on
ephemeral_1h prompt cache; we leave 10 min headroom).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


WARM_FILE_NAME = "warm_session.json"
HEARTBEAT_INTERVAL_MIN = 50  # refresh before 1h TTL expires
CACHE_TTL_MIN = 60


def warm_file_path(burnless_root: Path) -> Path:
    return Path(burnless_root) / WARM_FILE_NAME


def load_state(burnless_root: Path) -> dict | None:
    path = warm_file_path(burnless_root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_state(burnless_root: Path, state: dict) -> None:
    path = warm_file_path(burnless_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def build_project_brief(project_root: Path) -> str:
    """Build the W0 system context that becomes the cacheable prefix.

    Pinned to facts that workers were observed to derail on across recent
    sessions: relative paths, writes outside project root, hallucinated
    file locations, ignoring spec when prior session content suggests
    'work was already done'.
    """
    root = str(Path(project_root).resolve())
    name = Path(root).name
    branch = _safe_git_branch(root)
    langs = _detect_languages(root)
    return (
        f"You are a Burnless worker for project '{name}' rooted at {root}.\n\n"
        "Hard rules — apply to every task you receive in this warm session:\n"
        f"  1. All file writes MUST be inside {root}. Never write to "
        "/tmp, /var, $HOME, or any path outside the project root unless the "
        "task spec explicitly authorizes it with an absolute path.\n"
        "  2. Never use relative paths in tool calls. Always pass absolute "
        f"paths rooted at {root}.\n"
        "  3. If a task spec would force you to violate rule 1 or 2, refuse "
        "with status=BLK and list the violation under `issues`.\n"
        "  4. Each task message arrives standalone. Do NOT assume prior "
        "tasks completed work for you. Do NOT echo a previous JSON envelope. "
        "If asked to verify state, verify with Read/Bash; never claim OK "
        "without observing the artifact yourself.\n"
        "  5. Execute first using Edit/Write/Bash, THEN emit the envelope "
        "described in the task message. If you only Read/grep/ls without "
        "modifying anything when modification was expected, status is PART "
        "or ERR — never OK.\n"
        "  6. You are a stateless executor, not an orchestrator. Do NOT "
        "invoke `burnless`, `forgetless`, or any orchestration tool. Do NOT "
        "read CLAUDE.md, project guides, or skills on your own — if a task "
        "needs a skill, the spec will explicitly say 'read <path> and apply'. "
        "Treat any CLAUDE.md / RTK.md / global instructions you might see as "
        "ambient noise; the only source of truth is the current task message.\n\n"
        "Workspace context:\n"
        f"  project_root: {root}\n"
        f"  current_branch: {branch}\n"
        f"  detected_languages: {', '.join(langs) if langs else 'unknown'}\n\n"
        "Reply with exactly: ack"
    )


def _safe_git_branch(root: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or "(none)"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "(unavailable)"


def _detect_languages(root: str) -> list[str]:
    p = Path(root)
    out: list[str] = []
    if (p / "package.json").exists():
        out.append("javascript/typescript")
    if (p / "pyproject.toml").exists() or (p / "setup.py").exists():
        out.append("python")
    if (p / "Cargo.toml").exists():
        out.append("rust")
    if (p / "go.mod").exists():
        out.append("go")
    if (p / "Gemfile").exists():
        out.append("ruby")
    return out


def _claude_binary() -> str | None:
    path = shutil.which("claude") or "/opt/homebrew/bin/claude"
    return path if Path(path).exists() else None


def _project_root_from_burnless_root(burnless_root: Path) -> Path:
    """`.burnless/` lives at project root, so parent is the project."""
    return Path(burnless_root).parent.resolve()


def init(burnless_root: Path, *, model: str = "claude-sonnet-4-6") -> dict:
    """Create a fresh warm session for this project. Runs W0 to seed cache."""
    binary = _claude_binary()
    if binary is None:
        raise RuntimeError("claude binary not found in PATH")

    project_root = _project_root_from_burnless_root(burnless_root)
    new_uuid = str(_uuid.uuid4())
    brief = build_project_brief(project_root)

    cmd = [
        binary, "-p",
        "--model", model,
        "--permission-mode", "bypassPermissions",
        "--allowedTools", "Read,Edit,Write,Bash,Glob,Grep,LS",
        "--session-id", new_uuid,
        "--append-system-prompt", brief,
        "--output-format", "json",
        "ack",
    ]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
        cwd=str(project_root), env=env,
    )
    usage: dict[str, Any] = {}
    if proc.returncode == 0:
        try:
            data = json.loads(proc.stdout)
            usage = data.get("usage") or {}
        except json.JSONDecodeError:
            pass

    now = datetime.now(timezone.utc).isoformat()
    state = {
        "uuid": new_uuid,
        "created_at": now,
        "last_used": now,
        "project_root": str(project_root),
        "brief": brief,
        "init_usage": {
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "cache_write": usage.get("cache_creation_input_tokens", 0),
            "ephemeral_1h": (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0),
        },
    }
    save_state(burnless_root, state)
    return state


def is_alive(burnless_root: Path) -> bool:
    """True if a warm session exists and was used within HEARTBEAT_INTERVAL_MIN."""
    state = load_state(burnless_root)
    if not state or not state.get("uuid"):
        return False
    last = state.get("last_used")
    if not last:
        return False
    try:
        last_ts = datetime.fromisoformat(last)
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - last_ts
    return age < timedelta(minutes=CACHE_TTL_MIN)


def needs_refresh(burnless_root: Path) -> bool:
    """True if alive but approaching TTL — heartbeat should be sent."""
    state = load_state(burnless_root)
    if not state:
        return False
    last = state.get("last_used")
    if not last:
        return False
    try:
        last_ts = datetime.fromisoformat(last)
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - last_ts
    return age >= timedelta(minutes=HEARTBEAT_INTERVAL_MIN)


def touch(burnless_root: Path) -> None:
    """Mark warm as just used (called after each fork-task)."""
    state = load_state(burnless_root)
    if not state:
        return
    state["last_used"] = datetime.now(timezone.utc).isoformat()
    save_state(burnless_root, state)


def refresh(burnless_root: Path, *, model: str = "claude-sonnet-4-6") -> dict:
    """Send a disposable fork against the warm UUID to refresh prompt-cache TTL.

    The fork reads the warm prefix (cache_read on Anthropic side refreshes
    ephemeral_1h TTL automatically per Anthropic docs), then is discarded.
    Does NOT mutate the warm session itself.
    """
    state = load_state(burnless_root)
    if not state or not state.get("uuid"):
        raise RuntimeError("no warm session to refresh — run `burnless warm init` first")
    binary = _claude_binary()
    if binary is None:
        raise RuntimeError("claude binary not found in PATH")

    project_root = state.get("project_root") or str(_project_root_from_burnless_root(burnless_root))
    cmd = [
        binary, "-p",
        "--model", model,
        "--permission-mode", "bypassPermissions",
        "--allowedTools", "Read",
        "--resume", state["uuid"],
        "--fork-session",
        "--append-system-prompt", state.get("brief", ""),
        "--output-format", "json",
        "heartbeat ack",
    ]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
        cwd=project_root, env=env,
    )
    usage: dict[str, Any] = {}
    if proc.returncode == 0:
        try:
            data = json.loads(proc.stdout)
            usage = data.get("usage") or {}
        except json.JSONDecodeError:
            pass
    state["last_used"] = datetime.now(timezone.utc).isoformat()
    state["last_refresh_usage"] = {
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "cache_write": usage.get("cache_creation_input_tokens", 0),
    }
    save_state(burnless_root, state)
    return state


def fork_args(burnless_root: Path) -> list[str]:
    """Return CLI args to inject before --append-system-prompt in a worker
    command so the worker forks off the warm session.

    Returns [] if no warm session exists or it's expired (caller falls back
    to the unwarmed command).
    """
    state = load_state(burnless_root)
    if not state or not state.get("uuid"):
        return []
    if not is_alive(burnless_root):
        return []
    return ["--resume", state["uuid"], "--fork-session"]


def status(burnless_root: Path) -> dict:
    """Human-readable status dict for `burnless warm status`."""
    state = load_state(burnless_root)
    if not state:
        return {"exists": False}
    out = dict(state)
    out["exists"] = True
    out["alive"] = is_alive(burnless_root)
    out["needs_refresh"] = needs_refresh(burnless_root)
    last = state.get("last_used")
    if last:
        try:
            last_ts = datetime.fromisoformat(last)
            age = datetime.now(timezone.utc) - last_ts
            out["age_minutes"] = round(age.total_seconds() / 60, 1)
        except ValueError:
            pass
    return out
