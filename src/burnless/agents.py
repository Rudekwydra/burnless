from __future__ import annotations
import os
import shlex
import subprocess
import shutil
from pathlib import Path
from datetime import datetime, timezone


class AgentError(RuntimeError):
    pass


_VALID_SANDBOX = {"read-only", "workspace-write", "danger-full-access"}


def _strip_flag(parts: list[str], flag: str) -> list[str]:
    out: list[str] = []
    skip_next = False
    for token in parts:
        if skip_next:
            skip_next = False
            continue
        if token == flag:
            skip_next = True
            continue
        if token.startswith(flag + "="):
            continue
        out.append(token)
    return out


def _apply_codex_overrides(parts: list[str], agent_cfg: dict) -> list[str]:
    """Apply optional declarative overrides for codex tier (diamond)."""
    sandbox = agent_cfg.get("sandbox") or os.environ.get("BURNLESS_SANDBOX")
    workspace_root = agent_cfg.get("workspace_root") or os.environ.get("BURNLESS_WORKSPACE_ROOT")
    allow_net = agent_cfg.get("allow_net") or os.environ.get("BURNLESS_ALLOW_NET") in ("1", "true", "yes")

    if not (sandbox or workspace_root or allow_net):
        return parts
    if not parts or parts[0] != "codex":
        return parts

    out = list(parts)
    if sandbox:
        if sandbox not in _VALID_SANDBOX:
            raise AgentError(
                f"invalid agent sandbox={sandbox!r}; expected one of {sorted(_VALID_SANDBOX)}"
            )
        out = _strip_flag(out, "--sandbox")
        try:
            insert_at = out.index("exec") + 1
        except ValueError:
            insert_at = 1
        out[insert_at:insert_at] = ["--sandbox", sandbox]
    if allow_net and not agent_cfg.get("sandbox"):
        out = _strip_flag(out, "--sandbox")
        try:
            insert_at = out.index("exec") + 1
        except ValueError:
            insert_at = 1
        out[insert_at:insert_at] = ["--full-auto"]
    if workspace_root:
        root_path = str(Path(workspace_root).expanduser())
        out = _strip_flag(out, "--cd")
        out += ["--cd", root_path]
    return out


def resolve_command(agent_cfg: dict) -> list[str]:
    cmd = agent_cfg.get("command", "").strip()
    if not cmd:
        raise AgentError(f"agent missing 'command': {agent_cfg}")
    parts = shlex.split(cmd)
    parts = _apply_codex_overrides(parts, agent_cfg)
    return parts


def is_available(agent_cfg: dict) -> bool:
    parts = resolve_command(agent_cfg)
    return shutil.which(parts[0]) is not None


def run(agent_cfg: dict, prompt: str, *, timeout: int = 600, cwd: Path | None = None) -> dict:
    """Execute the agent CLI with `prompt` on stdin. Returns dict with stdout, stderr, returncode, duration."""
    parts = resolve_command(agent_cfg)
    if shutil.which(parts[0]) is None:
        raise AgentError(
            f"agent binary not found in PATH: {parts[0]} (configured for {agent_cfg.get('name')})"
        )
    started = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(
            parts,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired as e:
        raise AgentError(f"agent {agent_cfg.get('name')} timed out after {timeout}s") from e
    ended = datetime.now(timezone.utc)
    return {
        "agent": agent_cfg.get("name"),
        "command": parts,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_s": (ended - started).total_seconds(),
    }
