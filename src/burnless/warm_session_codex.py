"""Warm session pool for Burnless codex workers (byte-cache strategy).

Unlike the Claude warm session (--resume + --fork-session), codex caches via
OpenAI's prefix cache: identical byte sequences at the start of each request
get cached server-side. We seed the cache with a build_project_brief prefix,
then every subsequent call reuses it.

Empirical data (codex-cache-empirics-2026-05-23):
- Cache base TTL: >=600s (tested at 312s idle, still 7552 tokens cached)
- Partial variation first seen at 126s idle (secondary layer)
- Heartbeat interval: 84s (30% headroom under 126s)
- Cache metric: cached_input_tokens in turn.completed.usage (JSONL)
- CWD does not affect prefix cacheability

State lives at ~/.burnless/warm/codex/<model>.json.
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

from . import config

WARM_SUBDIR = "warm"
PROVIDER = "codex"
TTL_S = 300                      # conservative: base >=600s, we use 300s
HEARTBEAT_INTERVAL_S = 84        # per empirics above: partial layer starts drifting ~126s idle, 84s gives headroom and stays well under TTL_S
ISO_CWD_ROOT_NAME = "iso-cwd-codex"  # ~/.burnless/iso-cwd-codex/<uuid>/
DEFAULT_MODEL = config.DEFAULT_PROVIDER_MODELS["codex"]


def _pool_dir() -> Path:
    """Warm pool base dir. BURNLESS_WARM_DIR overrides $HOME for hermetic tests."""
    override = os.environ.get("BURNLESS_WARM_DIR")
    if override:
        return Path(override) / PROVIDER
    return Path.home() / ".burnless" / WARM_SUBDIR / PROVIDER


def warm_file_path(model: str, burnless_root: Path | None = None) -> Path:
    """Per-(provider, model) global pool location.

    Lives at ~/.burnless/warm/codex/<model>.json. The `burnless_root`
    argument is ignored — there is exactly one warm pool per (user, provider,
    model) that is forked by every worker in every project. Different models
    keep their own caches; no prune-by-drift.
    """
    safe_model = model.replace("/", "_").strip()
    return _pool_dir() / f"{safe_model}.json"


def list_warm_files() -> list[Path]:
    """Return all existing warm session files for this provider."""
    base = _pool_dir()
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


def _top_level_tree(root: str, max_entries: int = 80) -> str:
    """Two-level listing to push the cached prefix past the 1024-char threshold
    required for OpenAI prefix caching to activate."""
    p = Path(root)
    try:
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return "  (unreadable)"
    lines: list[str] = []
    for e in entries[:max_entries]:
        if e.is_dir():
            lines.append(f"  {e.name}/")
            try:
                sub = sorted(e.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
                for se in sub[:10]:
                    suffix = "/" if se.is_dir() else ""
                    lines.append(f"    {se.name}{suffix}")
                if len(sub) > 10:
                    lines.append(f"    ... ({len(sub) - 10} more)")
            except OSError:
                pass
        else:
            lines.append(f"  {e.name}")
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


def build_project_brief(project_root: Path) -> str:
    """Build the cacheable preamble sent as byte-identical prefix in every codex call.

    Neutral by design: no behavioral rules, just factual project context. The
    task spec is the only source of behavior. Must exceed 1024 chars to activate
    OpenAI prefix caching.
    """
    root = str(Path(project_root).resolve())
    name = Path(root).name
    branch = _safe_git_branch(root)
    langs = _detect_languages(root)
    tree = _top_level_tree(root)
    return (
        f"=== PROJECT CONTEXT (cacheable preamble) ===\n"
        f"Project: {name}\n"
        f"Root: {root}\n"
        f"Branch: {branch}\n"
        f"Languages: {', '.join(langs) if langs else 'unknown'}\n\n"
        f"Top-level layout:\n{tree}\n\n"
        f"=== END CONTEXT ===\n\n"
    )


def _parse_codex_usage(stdout_text: str) -> dict:
    """Extract usage dict from turn.completed event in codex --json output."""
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "turn.completed":
            return ev.get("usage", {}) or {}
    return {}


def _codex_binary() -> str | None:
    path = shutil.which("codex") or str(Path.home() / ".local" / "bin" / "codex")
    return path if path and Path(path).exists() else None


def _iso_cwd_codex(warm_uuid: str) -> Path:
    """Per-session isolated CWD outside any project tree so codex config
    discovery finds nothing project-specific."""
    p = Path.home() / ".burnless" / ISO_CWD_ROOT_NAME / warm_uuid
    p.mkdir(parents=True, exist_ok=True)
    return p


def _project_root_from_burnless_root(burnless_root: Path) -> Path:
    return Path(burnless_root).parent.resolve()


def init(burnless_root: Path, *, model: str = DEFAULT_MODEL) -> dict:
    """Seed the codex prefix cache for this project. Runs one call to warm."""
    existing = load_state(burnless_root, model)
    if existing and is_alive(burnless_root, model):
        return existing

    binary = _codex_binary()
    if binary is None:
        raise RuntimeError("codex binary not found in PATH")

    project_root = _project_root_from_burnless_root(burnless_root)
    new_uuid = str(_uuid.uuid4())
    iso_cwd = _iso_cwd_codex(new_uuid)
    brief = build_project_brief(project_root)
    user_msg = brief + "Reply with only: ack"

    cmd = [
        binary, "exec",
        "--cd", str(iso_cwd),
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox", "read-only",
        "--json",
        "-o", "/dev/null",
        "-m", model,
        user_msg,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    usage = _parse_codex_usage(proc.stdout)

    now = datetime.now(timezone.utc).isoformat()
    state = {
        "uuid": new_uuid,
        "created_at": now,
        "last_used": now,
        "project_root": str(project_root),
        "brief": brief,
        "iso_cwd": str(iso_cwd),
        "model": model,
        "brief_hash": hashlib.sha256(brief.encode("utf-8")).hexdigest(),
        "init_usage": {
            "input": usage.get("input_tokens", 0),
            "cached": usage.get("cached_input_tokens", 0),
            "output": usage.get("output_tokens", 0),
        },
    }
    save_state(burnless_root, model, state)
    return state


def refresh(burnless_root: Path, *, model: str = DEFAULT_MODEL) -> dict:
    """Heartbeat ping to keep the codex prefix cache warm.

    Sends the byte-identical brief as user message prefix so cached_input_tokens
    stays active server-side.
    """
    state = load_state(burnless_root, model)
    if not state or not state.get("uuid"):
        raise RuntimeError("no warm codex session — run `burnless warm-codex init` first")
    binary = _codex_binary()
    if binary is None:
        raise RuntimeError("codex binary not found in PATH")

    iso_cwd = state.get("iso_cwd") or str(_iso_cwd_codex(state["uuid"]))
    brief = state.get("brief", "")
    user_msg = brief + "heartbeat ack"

    cmd = [
        binary, "exec",
        "--cd", iso_cwd,
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox", "read-only",
        "--json",
        "-o", "/dev/null",
        "-m", model,
        user_msg,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    usage = _parse_codex_usage(proc.stdout)

    state["last_used"] = datetime.now(timezone.utc).isoformat()
    state["last_refresh_usage"] = {
        "input": usage.get("input_tokens", 0),
        "cached": usage.get("cached_input_tokens", 0),
        "output": usage.get("output_tokens", 0),
    }
    save_state(burnless_root, model, state)
    return state


def is_alive(burnless_root: Path, model: str, ttl_s: int = TTL_S) -> bool:
    """True if a warm codex session exists and last_used is within TTL."""
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
    return age < timedelta(seconds=ttl_s)


def needs_refresh(burnless_root: Path, model: str, heartbeat_interval_s: int = HEARTBEAT_INTERVAL_S) -> bool:
    """True if alive but approaching the partial-drop window — send heartbeat."""
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
    return age >= timedelta(seconds=heartbeat_interval_s)


def cache_validity(burnless_root: Path, model: str, expected_brief: str) -> tuple[bool, str]:
    """Return (valid, reason). reason is empty if valid, else describes drift."""
    state = load_state(burnless_root, model)
    if not state:
        return (False, "no warm state")
    expected_hash = hashlib.sha256(expected_brief.encode("utf-8")).hexdigest()
    if state.get("brief_hash") != expected_hash:
        return (False, "brief drift (project layout / branch changed)")
    return (True, "")


def prune_stale(burnless_root: Path, model: str, expected_brief: str | None = None) -> tuple[bool, str]:
    """Prune state if TTL expired OR brief drift."""
    state = load_state(burnless_root, model)
    if not state:
        return (False, "")
    last = state.get("last_used")
    if not last:
        return (False, "")
    try:
        last_ts = datetime.fromisoformat(last)
    except ValueError:
        return (False, "")
    age = datetime.now(timezone.utc) - last_ts
    reason = ""
    if age >= timedelta(seconds=TTL_S):
        reason = f"TTL expired (age {int(age.total_seconds())}s)"
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


def worker_cwd(burnless_root: Path, model: str) -> str | None:
    """Isolated CWD path for worker subprocesses, or None if warm is dead."""
    state = load_state(burnless_root, model)
    if not state or not state.get("uuid"):
        return None
    if not is_alive(burnless_root, model):
        return None
    iso = state.get("iso_cwd")
    return iso if iso else None


def warm_flags(burnless_root: Path, model: str) -> list[str]:
    """CLI args to inject into a codex worker invocation to reuse warm iso-cwd.

    Returns [] if no warm session or session is expired (caller falls back).
    """
    iso = worker_cwd(burnless_root, model)
    if not iso:
        return []
    return [
        "--cd", iso,
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
    ]


def warm_brief(burnless_root: Path, model: str) -> str:
    """Return the cacheable preamble to prepend to worker user messages.

    Empty string if warm is not alive (caller falls back to no-warm path).
    """
    if not is_alive(burnless_root, model):
        return ""
    state = load_state(burnless_root, model)
    if not state:
        return ""
    return state.get("brief", "")


def warm_args(burnless_root: Path, model: str) -> list[str]:
    """Protocol alias: codex warm = byte-prefix flags."""
    return warm_flags(burnless_root, model)


def warm_prefix(burnless_root: Path, model: str) -> str:
    """Codex caches by identical byte-prefix; the stable brief IS the cached prefix."""
    return warm_brief(burnless_root, model)


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
            out["age_s"] = round(age.total_seconds(), 1)
            last_tok = state.get("last_refresh_usage") or state.get("init_usage") or {}
            cached = last_tok.get("cached", 0)
            input_tok = last_tok.get("input", 1)
            out["last_cache_ratio"] = round(cached / max(input_tok, 1), 3)
        except ValueError:
            pass
    return out


def explain(burnless_root: Path, model: str | None = None) -> dict:
    """Rich, pure view over status() for `burnless warm explain`.

    model=None -> {model: explain(model)} for every warm file (mirrors status()).
    Adds: provider, uuid_prefix, ttl_status, ttl_remaining_min, compaction_caution.
    Never raises; returns {"exists": False, "provider": "codex"} when absent.
    """
    if model is None:
        return {p.stem: explain(burnless_root, model=p.stem) for p in list_warm_files()}
    s = status(burnless_root, model)
    if not s.get("exists"):
        return {"exists": False, "provider": "codex", "model": model}
    out = dict(s)
    out["provider"] = "codex"
    out["model"] = model
    uuid = s.get("uuid") or ""
    out["uuid_prefix"] = uuid[:8]
    age_min = (s.get("age_s") / 60.0) if isinstance(s.get("age_s"), (int, float)) else None
    alive = s.get("alive")
    ttl_min = TTL_S / 60.0
    aging_threshold = HEARTBEAT_INTERVAL_S / 60.0
    if age_min is None or not alive or age_min >= ttl_min:
        out["ttl_status"] = "expired"
    elif age_min >= aging_threshold:
        out["ttl_status"] = "aging"
    else:
        out["ttl_status"] = "fresh"
    out["ttl_remaining_min"] = round(max(0.0, ttl_min - age_min), 1) if isinstance(age_min, (int, float)) else 0.0
    if out["ttl_status"] == "expired":
        out["compaction_caution"] = "warm prefix is cold; compaction is safe (no hot prefix to bust)"
    else:
        out["compaction_caution"] = "warm prefix is hot; a deep compaction may bust the cached prefix"
    out.setdefault("last_cache_ratio", s.get("last_cache_ratio", 0.0))
    return out
