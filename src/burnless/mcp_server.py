from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from . import paths, state as state_mod
from . import delegations, routing, live_runner, config as config_mod
from . import audit_graph
from .agents import resolve_command


server = Server("burnless")


@dataclass
class DelegateInput:
    text: str
    tier: Optional[str] = None
    project_root: Optional[str] = None


@dataclass
class RouteInput:
    text: str
    project_root: Optional[str] = None


@dataclass
class RunInput:
    id: str
    background: bool = False
    project_root: Optional[str] = None


@dataclass
class CapsuleInput:
    id: str
    project_root: Optional[str] = None


@dataclass
class ReadInput:
    id: str
    project_root: Optional[str] = None
    max_log_lines: int = 200


@dataclass
class StatusInput:
    id: Optional[str] = None
    project_root: Optional[str] = None


@dataclass
class AuditInput:
    delegation_id: Optional[str] = None
    session: bool = False
    project_root: Optional[str] = None


@dataclass
class MaestroInput:
    envelope: str
    compression_mode: Optional[str] = "tight"
    project_root: Optional[str] = None


def _resolve_root(project_root: Optional[str]) -> Optional[Path]:
    if project_root:
        root = Path(project_root) / ".burnless"
        return root if root.exists() else None
    return paths.find_root()


def _get_config(burnless_root: Path) -> dict:
    try:
        return config_mod.load(burnless_root / "config.yaml")
    except Exception:
        return {"agents": {}, "routing": {}, "brain_adapter": "anthropic"}


async def handle_delegate(text: str, tier: Optional[str] = None, project_root: Optional[str] = None) -> dict:
    if not text or not text.strip():
        return {"error": "invalid_input", "hint": "text must be non-empty"}

    burnless_root = _resolve_root(project_root)
    if burnless_root is None:
        return {"error": "no_burnless_root", "hint": "run `burnless init` in project root"}

    try:
        cfg = _get_config(burnless_root)
        state_path = burnless_root / "state.json"
        did = state_mod.alloc_delegation_id(state_path)
        st = state_mod.load(state_path)

        routed_tier = tier
        matched_kw = None
        routed_by = "manual"

        if not tier:
            rules = cfg.get("routing", {})
            routed_tier, matched_kw = routing.route(text, rules, default_tier="bronze")
            routed_by = "auto-route"

        agents_cfg = cfg.get("agents", {})
        if routed_tier not in agents_cfg:
            return {"error": "invalid_tier", "hint": f"tier '{routed_tier}' not configured in .burnless/config.yaml"}

        agent_info = agents_cfg.get(routed_tier, {})
        agent_name = agent_info.get("name", "haiku")

        md_content = delegations.render_delegation(
            delegation_id=did,
            goal="Task delegation",
            task=text,
            success="Deliver JSON output with status, files, validated, evidence",
            kind_hint="execution",
            agent_name=agent_name,
            tier=routed_tier,
            routed_by=routed_by,
        )

        deleg_path = burnless_root / "delegations" / f"{did}.md"
        delegations.write_delegation(deleg_path, md_content)

        state_mod.save(state_path, st)

        return {
            "id": did,
            "tier": routed_tier,
            "agent": agent_name,
            "routed_by": routed_by,
            "matched_keyword": matched_kw,
            "delegation_path": str(deleg_path),
            "created_at": delegations.datetime.now(delegations.timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"error": "config_error", "hint": str(e)}


async def handle_route(text: str, project_root: Optional[str] = None) -> dict:
    if not text or not text.strip():
        return {"error": "invalid_input", "hint": "text must be non-empty"}

    burnless_root = _resolve_root(project_root)
    if burnless_root is None:
        return {"error": "no_burnless_root", "hint": "run `burnless init` in project root"}

    try:
        cfg = _get_config(burnless_root)
        rules = cfg.get("routing", {})
        tier, kw = routing.route(text, rules, default_tier="bronze")
        agents_cfg = cfg.get("agents", {})
        agent_info = agents_cfg.get(tier, {})
        agent_name = agent_info.get("name", "haiku")

        decision = routing.decide_route(text, None, rules)
        return {
            "tier": tier,
            "agent": agent_name,
            "matched_keyword": kw or None,
            "default_used": not kw,
            "natural_tier": decision.natural_tier,
            "effective_tier": decision.effective_tier,
            "action": decision.action,
            "confidence": decision.confidence,
            "signals": [{"kind": s.kind, "value": s.value, "weight": s.weight} for s in decision.signals],
            "policy_source": decision.policy_source,
            "routing_rules_snapshot": rules,
        }
    except Exception as e:
        return {"error": "config_error", "hint": str(e)}


async def handle_run(id: str, background: bool = False, project_root: Optional[str] = None) -> dict:
    burnless_root = _resolve_root(project_root)
    if burnless_root is None:
        return {"error": "no_burnless_root", "hint": "run `burnless init` in project root"}

    deleg_path = burnless_root / "delegations" / f"{id}.md"
    if not deleg_path.exists():
        return {"error": "delegation_not_found", "hint": f"delegation {id} not found"}

    capsule_path = burnless_root / "capsules" / f"{id}.json"
    if capsule_path.exists():
        return {"error": "already_run", "hint": "use mcp__burnless__capsule to read prior result"}

    if background:
        return await _run_background(id, burnless_root)
    else:
        return await _run_sync(id, burnless_root)


async def _run_background(id: str, burnless_root: Path) -> dict:
    runs_dir = burnless_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    pid_file = runs_dir / f"{id}.pid"
    log_file = runs_dir / f"{id}.stdout.log"

    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            return {"error": "already_running", "hint": f"delegation {id} already running", "pid": pid}
        except (ValueError, ProcessLookupError):
            pass

    try:
        cfg = _get_config(burnless_root)
        cmd = resolve_command(burnless_root, "run", id)
        proc = subprocess.Popen(
            cmd,
            stdout=open(log_file, "a", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        pid_file.write_text(str(proc.pid))
        return {
            "id": id,
            "status": "running",
            "pid": proc.pid,
            "log_path": str(log_file),
            "envelope_path": str(runs_dir / f"{id}.envelope.json"),
        }
    except Exception as e:
        return {"error": "worker_failed", "hint": str(e)}


async def _run_sync(id: str, burnless_root: Path) -> dict:
    import io
    import contextlib
    import json as _json
    from . import paths as _paths
    from .cli import execute_delegation, RunOpts
    try:
        start = time.time()
        paths = _paths.paths_for(burnless_root)
        buf = io.StringIO()
        # execute_delegation prints to stdout/stderr; capture so the MCP JSON-RPC channel stays clean.
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = execute_delegation(RunOpts(id=id, progress="quiet", verbose=False), root=burnless_root)
        duration = time.time() - start

        summary_path = paths["temp"] / f"{id}.json"
        capsule_path = paths["capsules"] / f"{id}.json"
        envelope = None
        if summary_path.exists():
            try:
                envelope = _json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:
                envelope = None
        status = (envelope or {}).get("status") or ("OK" if rc == 0 else "ERR")
        return {
            "id": id,
            "status": status,
            "envelope": envelope,
            "capsule_path": str(capsule_path),
            "duration_seconds": duration,
        }
    except Exception as e:
        return {"error": "worker_failed", "hint": str(e)}


async def handle_capsule(id: str, project_root: Optional[str] = None) -> dict:
    burnless_root = _resolve_root(project_root)
    if burnless_root is None:
        return {"error": "no_burnless_root", "hint": "run `burnless init` in project root"}

    capsule_path = burnless_root / "capsules" / f"{id}.json"
    if not capsule_path.exists():
        return {"error": "capsule_not_ready", "hint": f"delegation {id} not yet complete"}

    try:
        capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
        return {"id": id, "capsule": capsule, "path": str(capsule_path)}
    except Exception as e:
        return {"error": "capsule_not_ready", "hint": str(e)}


async def handle_read(id: str, project_root: Optional[str] = None, max_log_lines: int = 200) -> dict:
    burnless_root = _resolve_root(project_root)
    if burnless_root is None:
        return {"error": "no_burnless_root", "hint": "run `burnless init` in project root"}

    capsule_path = burnless_root / "capsules" / f"{id}.json"
    if capsule_path.exists():
        try:
            capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
            return {"id": id, "source": "capsule", "content": capsule, "path": str(capsule_path)}
        except Exception:
            pass

    envelope_path = burnless_root / "runs" / f"{id}.envelope.json"
    if envelope_path.exists():
        try:
            envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
            return {"id": id, "source": "envelope", "content": envelope, "path": str(envelope_path)}
        except Exception:
            pass

    log_path = burnless_root / "runs" / f"{id}.stdout.log"
    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8") as f:
                lines = f.readlines()
                tail = "".join(lines[-max_log_lines:]) if len(lines) > max_log_lines else "".join(lines)
            return {
                "id": id,
                "source": "log",
                "content": tail,
                "path": str(log_path),
                "truncated": len(lines) > max_log_lines,
            }
        except Exception:
            pass

    return {"error": "delegation_not_found", "hint": f"no data found for delegation {id}"}


async def handle_status(id: Optional[str] = None, project_root: Optional[str] = None) -> dict:
    burnless_root = _resolve_root(project_root)
    if burnless_root is None:
        return {"error": "no_burnless_root", "hint": "run `burnless init` in project root"}

    if id:
        return _status_per_delegation(id, burnless_root)
    else:
        return _status_project_wide(burnless_root)


def _status_per_delegation(id: str, burnless_root: Path) -> dict:
    deleg_path = burnless_root / "delegations" / f"{id}.md"
    capsule_path = burnless_root / "capsules" / f"{id}.json"
    pid_file = burnless_root / "runs" / f"{id}.pid"
    log_file = burnless_root / "runs" / f"{id}.stdout.log"

    state_dict = {
        "id": id,
        "state": "missing",
        "pid": None,
        "capsule_status": None,
        "log_size_bytes": None,
        "started_at": None,
        "finished_at": None,
    }

    if not deleg_path.exists():
        return state_dict

    state_dict["state"] = "not_started"

    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            state_dict["state"] = "running"
            state_dict["pid"] = pid
        except (ValueError, ProcessLookupError):
            state_dict["state"] = "failed"
            state_dict["pid"] = None

    if capsule_path.exists():
        state_dict["state"] = "done"
        try:
            capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
            state_dict["capsule_status"] = capsule.get("status", "OK")
        except Exception:
            pass

    if log_file.exists():
        state_dict["log_size_bytes"] = log_file.stat().st_size

    return state_dict


def _status_project_wide(burnless_root: Path) -> dict:
    capsules_dir = burnless_root / "capsules"
    delegations_dir = burnless_root / "delegations"
    runs_dir = burnless_root / "runs"

    capsules_count = len(list(capsules_dir.glob("*.json"))) if capsules_dir.exists() else 0
    cfg = _get_config(burnless_root)

    pending = []
    if delegations_dir.exists():
        for deleg_file in delegations_dir.glob("*.md"):
            deleg_id = deleg_file.stem
            capsule = burnless_root / "capsules" / f"{deleg_id}.json"
            if not capsule.exists():
                pending.append(deleg_id)

    running_now = []
    if runs_dir.exists():
        for pid_file in runs_dir.glob("*.pid"):
            deleg_id = pid_file.stem
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                running_now.append({"id": deleg_id, "pid": pid, "tier": "unknown"})
            except (ValueError, ProcessLookupError):
                pass

    last_capsule = None
    if capsules_dir.exists():
        capsules = sorted(capsules_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if capsules:
            try:
                last_cap = json.loads(capsules[0].read_text(encoding="utf-8"))
                last_capsule = {
                    "id": capsules[0].stem,
                    "created_at": capsules[0].stat().st_mtime,
                    "status": last_cap.get("status", "OK"),
                }
            except Exception:
                pass

    return {
        "project_root": str(burnless_root.parent),
        "capsules_count": capsules_count,
        "pending_delegations": pending,
        "running_now": running_now,
        "last_capsule": last_capsule,
        "config": cfg,
    }


async def handle_audit(delegation_id: Optional[str] = None, session: bool = False, project_root: Optional[str] = None) -> dict:
    burnless_root = _resolve_root(project_root)
    if burnless_root is None:
        return {"error": "no_burnless_root", "hint": "run `burnless init` in project root"}

    did = None if session else delegation_id
    records = audit_graph.read_records(burnless_root.parent, did)
    return {"records": records}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="delegate",
            description="Create a delegation and auto-route to tier",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Task description"},
                    "tier": {"type": ["string", "null"], "description": "Tier: bronze, silver, gold, diamond. None = auto-route"},
                    "project_root": {"type": ["string", "null"], "description": "Abs path to project root"},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="route",
            description="Preview tier routing without creating delegation",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Task description"},
                    "project_root": {"type": ["string", "null"], "description": "Abs path to project root"},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="run",
            description="Execute a delegation (sync or background)",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Delegation ID (e.g. d042)"},
                    "background": {"type": "boolean", "description": "Run detached (default false)"},
                    "project_root": {"type": ["string", "null"], "description": "Abs path to project root"},
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="capsule",
            description="Read finalized delegation result",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Delegation ID"},
                    "project_root": {"type": ["string", "null"], "description": "Abs path to project root"},
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="read",
            description="Read delegation output (3-paths fallback)",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Delegation ID"},
                    "project_root": {"type": ["string", "null"], "description": "Abs path to project root"},
                    "max_log_lines": {"type": "integer", "description": "Max log lines to return (default 200)"},
                },
                "required": ["id"],
            },
        ),
        Tool(
            name="status",
            description="Project health or per-delegation status",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": ["string", "null"], "description": "Delegation ID. None = project-wide"},
                    "project_root": {"type": ["string", "null"], "description": "Abs path to project root"},
                },
            },
        ),
        Tool(
            name="audit",
            description="Read and render audit graph records",
            inputSchema={
                "type": "object",
                "properties": {
                    "delegation_id": {"type": ["string", "null"], "description": "Delegation ID. None with session=true"},
                    "session": {"type": "boolean", "description": "Show all records (default false)"},
                    "project_root": {"type": ["string", "null"], "description": "Abs path to project root"},
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handlers = {
        "delegate": handle_delegate,
        "route": handle_route,
        "run": handle_run,
        "capsule": handle_capsule,
        "read": handle_read,
        "status": handle_status,
        "audit": handle_audit,
    }

    handler = handlers.get(name)
    if not handler:
        result = {"error": "unknown_tool", "hint": f"tool '{name}' not found"}
    else:
        result = await handler(**arguments)

    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    if "--check" in sys.argv:
        print("ok")
        raise SystemExit(0)
    asyncio.run(main())
