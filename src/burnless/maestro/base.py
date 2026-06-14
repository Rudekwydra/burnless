"""Warm MAESTRO base session (v1 glue).

Mirrors warm_session.init() but maestro-flavored: the cached prefix carries
the slim partner role (_design/maestro_v1/maestro_role.md) and the session
is tool-less by policy — tool defs stay PRESENT as cache anchor, usage is
blocked via --disallowedTools MAESTRO_DISALLOWED.

State persists at ~/.burnless/warm/claude/maestro-<model>.json by reusing
warm_session's load/save/is_alive machinery with a "maestro-<model>" key
(warm_file_path passes unknown model names through verbatim), so heartbeat,
ghost-pruning and TTL logic come for free.

See _design/FABLE_READINESS_AND_VERBOSE_2026-06-09.md Part 1.2 item 2.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import warm_session
from .session_runner import MAESTRO_DISALLOWED

# Minimal embedded fallback when _design/maestro_v1/maestro_role.md is absent
# (wheel install). The on-disk role is canonical.
_FALLBACK_MAESTRO_ROLE = (
    "You are the Burnless MAESTRO — a tool-less partner. You decide and "
    "delegate; you never execute (tools visible but blocked by policy). "
    "To delegate emit one line per task: "
    "`del T<id> {tier} {action} {target} :: {spec}` (tier brz|slv|gld; "
    "spec telegraphic, 40-120 tok). Worker results come back as capsule "
    "lines `{tier} {action} {target} :: {OK|PART|BLK|ERR} ... [ref:...]` — "
    "treat them as authoritative state. Terse, no hype, no praise."
)


def _state_key(model: str) -> str:
    return f"maestro-{model}"


def _load_maestro_role(project_root: Path) -> str:
    role_path = project_root / "_design" / "maestro_v1" / "maestro_role.md"
    try:
        return role_path.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACK_MAESTRO_ROLE


def maestro_iso_cwd(burnless_root: Path, model: str) -> str | None:
    """iso-cwd of the live maestro base (runner must execute there so
    --resume finds the session jsonl). None when no live base exists."""
    state = warm_session.load_state(burnless_root, _state_key(model))
    if not state or not state.get("uuid"):
        return None
    return str(warm_session._iso_cwd(state["uuid"]))


def maestro_base_init(burnless_root: Path, model: str) -> str:
    """Create (or reuse) the warm maestro base for `model`. Returns base uuid."""
    key = _state_key(model)
    existing = warm_session.load_state(burnless_root, key)
    if existing and existing.get("uuid") and warm_session.is_alive(burnless_root, key):
        warm_session.touch(burnless_root, key)
        return existing["uuid"]

    binary = warm_session._claude_binary()
    if binary is None:
        raise RuntimeError("claude binary not found in PATH")

    project_root = Path(burnless_root).parent.resolve()
    role = _load_maestro_role(project_root)
    new_uuid = str(_uuid.uuid4())

    cmd = [
        binary, "-p",
        "--model", model,
        "--permission-mode", "bypassPermissions",
        # tool defs PRESENT (cache anchor); execution blocked by policy:
        "--disallowedTools", MAESTRO_DISALLOWED,
        "--session-id", new_uuid,
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources", "project,local",
        "--exclude-dynamic-system-prompt-sections",
        "--append-system-prompt", role,
        "--output-format", "json",
        "ack",
    ]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
        cwd=str(warm_session._iso_cwd(new_uuid)), env=env,
    )
    usage: dict = {}
    if proc.returncode == 0:
        try:
            usage = (json.loads(proc.stdout) or {}).get("usage") or {}
        except json.JSONDecodeError:
            pass
    elif proc.returncode != 0:
        raise RuntimeError(
            f"maestro base init failed (rc={proc.returncode}): "
            f"{(proc.stderr or proc.stdout)[:300]}"
        )

    now = datetime.now(timezone.utc).isoformat()
    state = {
        "uuid": new_uuid,
        "created_at": now,
        "last_used": now,
        "project_root": str(project_root),
        "model": model,
        "kind": "maestro",
        "brief": role,
        "brief_hash": hashlib.sha256(role.encode("utf-8")).hexdigest(),
        "init_usage": {
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "cache_write": usage.get("cache_creation_input_tokens", 0),
            "ephemeral_1h": (usage.get("cache_creation") or {}).get(
                "ephemeral_1h_input_tokens", 0
            ),
        },
    }
    warm_session.save_state(burnless_root, key, state)
    return new_uuid
