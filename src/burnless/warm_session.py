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

    Neutral by design: no role identity, no behavioral rules. Just factual
    project context (path, branch, languages, top-level tree). The task spec
    is the only source of behavior. Worker hygiene is enforced upstream via
    CLI flags (no CLAUDE.md auto-discovery, no hooks, no skills), not by
    asking the model to ignore them.
    """
    root = str(Path(project_root).resolve())
    name = Path(root).name
    branch = _safe_git_branch(root)
    langs = _detect_languages(root)
    tree = _top_level_tree(root)
    return (
        f"Project: {name}\n"
        f"Root: {root}\n"
        f"Branch: {branch}\n"
        f"Languages: {', '.join(langs) if langs else 'unknown'}\n\n"
        f"Top-level layout:\n{tree}\n\n"
        "Each user message after this is an independent task to execute with your tools.\n\n"
        "Reply with exactly: ack"
    )


def _top_level_tree(root: str, max_entries: int = 80) -> str:
    """One-level listing of the project root, sized to push the cached prefix
    past the 1024-token threshold needed to activate 1h prompt caching."""
    p = Path(root)
    try:
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return "  (unreadable)"
    lines: list[str] = []
    for e in entries[:max_entries]:
        suffix = "/" if e.is_dir() else ""
        lines.append(f"  {e.name}{suffix}")
    if len(entries) > max_entries:
        lines.append(f"  ... ({len(entries) - max_entries} more)")
    return "\n".join(lines) if lines else "  (empty)"


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


def _iso_cwd(warm_uuid: str) -> Path:
    """Per-warm-session isolated CWD living outside any project tree, so claude
    code's CLAUDE.md auto-discovery walk-up finds nothing project-specific.
    Workers run here and address project files via absolute paths."""
    p = Path.home() / ".burnless" / "iso-cwd" / warm_uuid
    p.mkdir(parents=True, exist_ok=True)
    return p


def worker_cwd(burnless_root: Path) -> str | None:
    """CWD that worker subprocesses should run in to avoid project CLAUDE.md
    contamination. Returns the iso-cwd for the live warm session, or None when
    no warm exists (caller falls back to the project root)."""
    state = load_state(burnless_root)
    if not state or not state.get("uuid"):
        return None
    if not is_alive(burnless_root):
        return None
    return str(_iso_cwd(state["uuid"]))


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
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources", "project,local",
        "--exclude-dynamic-system-prompt-sections",
        "--append-system-prompt", brief,
        "--output-format", "json",
        "ack",
    ]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    # Init the warm session in an isolated CWD outside any project tree so
    # claude code's CLAUDE.md auto-discovery does NOT bake the project's
    # CLAUDE.md into the cached prefix. Worker forks resume from the same
    # iso-cwd path and read the same (clean) cached prefix.
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
        cwd=str(_iso_cwd(new_uuid)), env=env,
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


def session_exists_on_disk(burnless_root: Path) -> bool:
    """Check whether the warm session's jsonl file actually exists in
    ~/.claude/projects/. Claude code stores resumable sessions at
    ~/.claude/projects/<dashed-cwd>/<session-uuid>.jsonl. We glob across
    project dirs rather than trying to reconstruct the exact dashed-path
    encoding, which has edge cases (dots, hidden dirs).
    """
    state = load_state(burnless_root)
    if not state or not state.get("uuid"):
        return False
    uuid = state["uuid"]
    projects_root = Path.home() / ".claude" / "projects"
    if not projects_root.exists():
        return False
    # Glob: any project dir containing <uuid>.jsonl
    matches = list(projects_root.glob(f"*/{uuid}.jsonl"))
    return len(matches) > 0


def is_alive(burnless_root: Path) -> bool:
    """True if warm session exists, within TTL, AND session jsonl is on disk."""
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
    if age >= timedelta(minutes=CACHE_TTL_MIN):
        return False
    # NEW: verify jsonl actually exists
    return session_exists_on_disk(burnless_root)


def prune_ghost(burnless_root: Path) -> bool:
    """If warm state references a session that no longer exists on disk,
    remove the state file. Returns True if pruned, False otherwise.
    Idempotent + safe to call before any dispatch.
    """
    state = load_state(burnless_root)
    if not state or not state.get("uuid"):
        return False
    if session_exists_on_disk(burnless_root):
        return False
    # Ghost — prune state file
    path = warm_file_path(burnless_root)
    try:
        path.unlink()
        return True
    except OSError:
        return False


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
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources", "project,local",
        "--exclude-dynamic-system-prompt-sections",
        "--append-system-prompt", state.get("brief", ""),
        "--output-format", "json",
        "heartbeat ack",
    ]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    # Refresh from the same iso-cwd used at init so --resume finds the jsonl.
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
        cwd=str(_iso_cwd(state["uuid"])), env=env,
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
