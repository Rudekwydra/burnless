"""Warm session pool for Burnless workers — GLOBAL, cross-project.

Workers invoked via `claude -p --resume <warm_uuid> --fork-session` inherit a
cached prefix from a SINGLE warm session shared across every project and every
window. Boot warmer pays the cold once per user (~$0.03), every subsequent
task in any project forks the warm prefix and pays only the new payload
(~$0.003-0.006 per task instead of $0.030).

State lives at ~/.burnless/warm/claude/<model>.json (GLOBAL, not per-project):
    {
      "uuid": "<uuid4>",
      "created_at": "<iso8601>",
      "last_used": "<iso8601>",
      "model": "<claude-sonnet-4-6>",
      "brief": "<W0 neutral brief>"
    }

The session jsonl itself lives where Claude Code stores it (path-derived under
~/.claude/projects/<iso-cwd-dashes>/<uuid>.jsonl) — we only keep the UUID. The
iso-cwd at ~/.burnless/iso-cwd/<warm_uuid>/ is the worker's clean working
directory (no CLAUDE.md from any project leaks in).

Heartbeat policy: refresh when last_used > 50 minutes ago (TTL is 1h on
ephemeral_1h prompt cache; we leave 10 min headroom).

Enforces [[rule-worker-never-fresh-2026-05-24]]: no worker ever spawns cold.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config


WARM_SUBDIR = "warm"
PROVIDER = "claude"
HEARTBEAT_INTERVAL_MIN = 59  # sliding TTL resets on each read, 1min margin with poll_interval=30s
CACHE_TTL_MIN = 60


def warm_file_path(model: str, burnless_root: Path | None = None) -> Path:
    """Per-(provider, model) global pool location.

    Lives at ~/.burnless/warm/claude/<model>.json. The `burnless_root`
    argument is ignored — there is exactly one warm pool per (user, provider,
    model) that is forked by every worker in every project. Different models
    keep their own caches; no prune-by-drift.
    """
    m = config.normalize_model(model) or model
    safe_model = m.replace("/", "_").strip()
    return Path.home() / ".burnless" / WARM_SUBDIR / PROVIDER / f"{safe_model}.json"


def list_warm_files() -> list[Path]:
    """Return all existing warm session files for this provider."""
    base = Path.home() / ".burnless" / WARM_SUBDIR / PROVIDER
    if not base.is_dir():
        return []
    return sorted(base.glob("*.json"))


def load_state(burnless_root: Path, model: str) -> dict | None:
    path = warm_file_path(model, burnless_root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_state(burnless_root: Path, model: str, state: dict) -> None:
    path = warm_file_path(model, burnless_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def build_project_brief(project_root: Path) -> str:
    """Build the W0 system context that becomes the cacheable prefix.

    GLOBAL warm pool — brief is project-agnostic so the same warm session
    can serve any worker in any project. project_root is accepted only for
    signature compatibility; it is NOT used.

    The brief contains only generic worker-hygiene context that applies
    universally: role neutrality, output discipline, no CLAUDE.md, no
    hooks, no skills (enforced upstream via CLI flags anyway).
    """
    return (
        "You are a Burnless worker invoked via `claude -p --resume <warm_uuid> "
        "--fork-session`. Your role and behavior are determined entirely by the "
        "task spec you receive. Do not assume a persona, do not narrate, do not "
        "summarize at the end. Execute the spec exactly as written and emit the "
        "result envelope when done. Hooks, skills, and CLAUDE.md auto-discovery "
        "are disabled via CLI flags upstream — do not attempt to invoke them.\n"
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


def worker_cwd(burnless_root: Path, model: str) -> str | None:
    """CWD that worker subprocesses should run in to avoid project CLAUDE.md
    contamination. Returns the iso-cwd for the live warm session, or None when
    no warm exists (caller falls back to the project root)."""
    state = load_state(burnless_root, model)
    if not state or not state.get("uuid"):
        return None
    if not is_alive(burnless_root, model):
        return None
    return str(_iso_cwd(state["uuid"]))


def _project_root_from_burnless_root(burnless_root: Path) -> Path:
    """`.burnless/` lives at project root, so parent is the project."""
    return Path(burnless_root).parent.resolve()


def init(burnless_root: Path, *, model: str = config.DEFAULT_PROVIDER_MODELS["claude"]) -> dict:
    """Create a fresh warm session for this project. Runs W0 to seed cache."""
    existing = load_state(burnless_root, model)
    if existing and is_alive(burnless_root, model):
        return existing

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
        "model": model,
        "brief_hash": hashlib.sha256(brief.encode("utf-8")).hexdigest(),
        "init_usage": {
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "cache_write": usage.get("cache_creation_input_tokens", 0),
            "ephemeral_1h": (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0),
        },
    }
    save_state(burnless_root, model, state)
    return state


def session_exists_on_disk(burnless_root: Path, model: str) -> bool:
    """Check whether the warm session's jsonl file actually exists in
    ~/.claude/projects/. Claude code stores resumable sessions at
    ~/.claude/projects/<dashed-cwd>/<session-uuid>.jsonl. We glob across
    project dirs rather than trying to reconstruct the exact dashed-path
    encoding, which has edge cases (dots, hidden dirs).
    """
    state = load_state(burnless_root, model)
    if not state or not state.get("uuid"):
        return False
    uuid = state["uuid"]
    projects_root = Path.home() / ".claude" / "projects"
    if not projects_root.exists():
        return False
    # Glob: any project dir containing <uuid>.jsonl
    matches = list(projects_root.glob(f"*/{uuid}.jsonl"))
    return len(matches) > 0


def is_alive(burnless_root: Path, model: str) -> bool:
    """True if warm session exists, within TTL, AND session jsonl is on disk."""
    state = load_state(burnless_root, model)
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
    return session_exists_on_disk(burnless_root, model)


def cache_validity(burnless_root: Path, model: str, expected_brief: str) -> tuple[bool, str]:
    """Return (valid, reason). reason is empty if valid, else describes drift."""
    state = load_state(burnless_root, model)
    if not state:
        return (False, "no warm state")
    expected_hash = hashlib.sha256(expected_brief.encode("utf-8")).hexdigest()
    if state.get("brief_hash") != expected_hash:
        return (False, "brief drift (project layout / branch changed)")
    return (True, "")


def prune_ghost(burnless_root: Path, model: str, expected_brief: str | None = None) -> tuple[bool, str]:
    """Prune state if session jsonl missing from disk, or brief drift."""
    state = load_state(burnless_root, model)
    if not state or not state.get("uuid"):
        return (False, "")
    if not session_exists_on_disk(burnless_root, model):
        reason = "session jsonl missing from disk"
    elif expected_brief is not None:
        valid, drift_reason = cache_validity(burnless_root, model, expected_brief)
        if valid:
            return (False, "")
        reason = drift_reason
    else:
        return (False, "")
    path = warm_file_path(model, burnless_root)
    try:
        path.unlink()
        return (True, reason)
    except OSError:
        return (False, "")


def needs_refresh(burnless_root: Path, model: str) -> bool:
    """True if alive but approaching TTL — heartbeat should be sent."""
    state = load_state(burnless_root, model)
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


def touch(burnless_root: Path, model: str) -> None:
    """Mark warm as just used (called after each fork-task)."""
    state = load_state(burnless_root, model)
    if not state:
        return
    state["last_used"] = datetime.now(timezone.utc).isoformat()
    save_state(burnless_root, model, state)


def refresh(burnless_root: Path, *, model: str = config.DEFAULT_PROVIDER_MODELS["claude"]) -> dict:
    """Send a disposable fork against the warm UUID to refresh prompt-cache TTL.

    The fork reads the warm prefix (cache_read on Anthropic side refreshes
    ephemeral_1h TTL automatically per Anthropic docs), then is discarded.
    Does NOT mutate the warm session itself.
    """
    state = load_state(burnless_root, model)
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
    save_state(burnless_root, model, state)
    return state


def fork_args(burnless_root: Path, model: str) -> list[str]:
    """Return CLI args to inject before --append-system-prompt in a worker
    command so the worker forks off the warm session.

    Returns [] if no warm session exists or it's expired (caller falls back
    to the unwarmed command).
    """
    state = load_state(burnless_root, model)
    if not state or not state.get("uuid"):
        return []
    if not is_alive(burnless_root, model):
        return []
    return ["--resume", state["uuid"], "--fork-session"]


warm_args = fork_args  # protocol alias: claude warm = session fork flags


def warm_prefix(burnless_root: Path, model: str) -> str:
    """Claude caches via session fork, not a prompt prefix."""
    return ""


def status(burnless_root: Path, model: str | None = None) -> dict:
    """If model is None, return {'<model>': <status_dict>} for all warm files.
    If model is given, return the status_dict for that specific model only.
    """
    if model is None:
        result = {}
        for p in list_warm_files():
            m = p.stem
            result[m] = status(burnless_root, model=m)
        return result
    state = load_state(burnless_root, model)
    if not state:
        return {"exists": False}
    out = dict(state)
    out["exists"] = True
    out["alive"] = is_alive(burnless_root, model)
    out["needs_refresh"] = needs_refresh(burnless_root, model)
    last = state.get("last_used")
    if last:
        try:
            last_ts = datetime.fromisoformat(last)
            age = datetime.now(timezone.utc) - last_ts
            out["age_minutes"] = round(age.total_seconds() / 60, 1)
        except ValueError:
            pass
    return out
