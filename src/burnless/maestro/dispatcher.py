from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import agents as agents_mod
from ..codec.glossary_loader import load_glossary
from ..routing import modulate_by_compression
from . import counter, exec_log


@dataclass
class DelegateSpec:
    id: int
    tier: str
    action: str
    target: str
    spec: str
    raw_line: str


TIER_ALIASES = {
    "brz": "bronze",
    "bronze": "bronze",
    "slv": "silver",
    "silver": "silver",
    "dia": "diamond",
    "diamond": "diamond",
    "gld": "gold",
    "gold": "gold",
}
CAPSULE_RE = re.compile(
    r"^(?:[+~]?(?:gld|slv|brz|dia|gold|silver|bronze|diamond))\s+"
    r"\w+\s+[\w/_.-]+\s+::\s+(?:OK|PART|BLK|ERR)\b.*"
)
DELEGATE_RE = re.compile(
    r"^del(?:(?:→?T(\d+))|(?:\s+T(\d+)))?\s+(\w+)\s+(\w+)\s+([\w/_.-]+)\s*::\s*(.+)$"
)
DELEGATE_SHORT_RE = re.compile(r"^del\s+(\w+)\s+([\w/_.-]+)\s*::\s*(.+)$")


def parse_delegates(delegate_lines: list[str], burnless_root: Path) -> list[DelegateSpec]:
    specs: list[DelegateSpec] = []
    for raw in delegate_lines:
        line = raw.strip()
        if not line:
            continue
        match = DELEGATE_RE.match(line)
        if match:
            task_id_compact, task_id_spaced, tier, action, target, spec_text = match.groups()
            task_id_raw = task_id_compact or task_id_spaced
            task_id = int(task_id_raw) if task_id_raw else counter.next_id(burnless_root)
            specs.append(
                DelegateSpec(
                    id=task_id,
                    tier=_short_tier(tier),
                    action=action,
                    target=target,
                    spec=spec_text,
                    raw_line=line,
                )
            )
            continue
        short_match = DELEGATE_SHORT_RE.match(line)
        if short_match:
            tier, target, spec_text = short_match.groups()
            specs.append(
                DelegateSpec(
                    id=counter.next_id(burnless_root),
                    tier=_short_tier(tier),
                    action="val",
                    target=target,
                    spec=spec_text,
                    raw_line=line,
                )
            )
    return specs


def run_all(
    delegate_lines: list[str],
    *,
    burnless_root: Path,
    project_root: Path,
    config: dict,
) -> list[str]:
    capsules: list[str] = []
    for spec in parse_delegates(delegate_lines, burnless_root):
        capsules.append(
            run_delegate(
                spec,
                burnless_root=burnless_root,
                project_root=project_root,
                config=config,
            )
        )
    return capsules


def run_delegate(
    spec: DelegateSpec,
    *,
    burnless_root: Path,
    project_root: Path,
    config: dict,
) -> str:
    brain_tier = _config_tier(spec.tier)
    compression_mode = ((config.get("compression") or {}).get("mode") or "balanced").lower()
    final_tier, modulation_reason = modulate_by_compression(brain_tier, "", compression_mode)
    if modulation_reason:
        print(f"  · {modulation_reason}")
    if final_tier != brain_tier or compression_mode != "balanced":
        detail = f" ({modulation_reason})" if modulation_reason else ""
        print(f"  [routing] Brain said {spec.tier} → Burnless using {final_tier}{detail}")

    tier_key = final_tier
    agent_cfg = (config.get("agents") or {}).get(tier_key)
    if not agent_cfg:
        log_path = exec_log.create(
            burnless_root,
            spec.id,
            parent_capsule=spec.raw_line,
            tier=tier_key,
            model="unknown",
        )
        return _finalize_error(
            log_path,
            spec,
            f"agent tier {tier_key} missing",
            status="BLK",
        )

    command_text = agent_cfg.get("command", "")
    model = _model_from_command(command_text) or agent_cfg.get("name") or tier_key
    parent_capsule = spec.raw_line
    log_path = exec_log.create(
        burnless_root,
        spec.id,
        parent_capsule=parent_capsule,
        tier=tier_key,
        model=model,
    )

    try:
        parts = _worker_command(agent_cfg, tier_key)
    except Exception as e:
        return _finalize_error(log_path, spec, f"command error: {e}")

    if shutil.which(parts[0]) is None:
        return _finalize_error(log_path, spec, f"worker binary not found: {parts[0]}", status="BLK")

    user_message = f"del T{spec.id} {tier_key} {spec.action} {spec.target} :: {spec.spec}"
    system_prompt = _worker_system_prompt(project_root)
    run_parts, stdin = _inject_system_prompt(parts, system_prompt, user_message)

    try:
        proc = subprocess.run(
            run_parts,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=int(config.get("maestro", {}).get("worker_timeout_s", 900)),
            cwd=str(project_root),
        )
    except subprocess.TimeoutExpired as e:
        return _finalize_error(
            log_path,
            spec,
            f"worker timed out after {e.timeout}s",
            status="ERR",
        )
    except OSError as e:
        return _finalize_error(log_path, spec, str(e), status="ERR")

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    capsule = _last_capsule(stdout)
    status = _capsule_status(capsule) if capsule else ("ERR" if proc.returncode else "PART")
    if not capsule:
        capsule = (
            f"{spec.tier} {spec.action} {spec.target} :: {status} "
            f"missing worker capsule [ref:exec/T{spec.id:04d}]"
        )
    transcript = _transcript(run_parts, user_message, stdout, stderr, proc.returncode)
    exec_log.finalize(
        log_path,
        status=status,
        files_touched=[],
        validations=[],
        issues=[] if status == "OK" else [f"returncode={proc.returncode}"],
        transcript=transcript,
        ended=datetime.now(timezone.utc),
    )
    return _ensure_exec_ref(capsule, spec.id)


def _worker_command(agent_cfg: dict, tier_key: str) -> list[str]:
    parts = agents_mod.resolve_command(agent_cfg)
    if not parts:
        raise agents_mod.AgentError("empty agent command")
    exe = Path(parts[0]).name
    if exe == "claude":
        if "-p" not in parts and "--print" not in parts:
            parts.append("-p")
        if "--allowedTools" not in parts and not any(p.startswith("--allowedTools=") for p in parts):
            tools = "Read,Bash,Grep" if tier_key == "bronze" else "Read,Edit,Write,Bash,Glob,Grep,LS"
            parts.extend(["--allowedTools", tools])
    return parts


def _inject_system_prompt(parts: list[str], system_prompt: str, user_message: str) -> tuple[list[str], str]:
    if Path(parts[0]).name == "claude":
        flag = _claude_system_flag(parts[0])
        if flag:
            return [*parts, flag, system_prompt], user_message
    return parts, system_prompt + "\n\n---\n\n" + user_message


def _claude_system_flag(executable: str) -> str | None:
    try:
        proc = subprocess.run(
            [executable, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    help_text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if "--system-prompt" in help_text:
        return "--system-prompt"
    if "--system" in help_text:
        return "--system"
    return None


def _worker_system_prompt(project_root: Path) -> str:
    role_path = project_root / "_design" / "maestro_v1" / "worker_role.md"
    role_text = role_path.read_text(encoding="utf-8")
    glossary = load_glossary(project_root)
    return glossary + "\n\n---\n\n" + role_text


def _last_capsule(stdout: str) -> str | None:
    capsule = None
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")
    for raw_line in stdout.splitlines():
        line = ansi_re.sub("", raw_line).strip()
        if CAPSULE_RE.match(line):
            capsule = line
    return capsule


def _capsule_status(capsule: str | None) -> str:
    if not capsule:
        return "ERR"
    match = re.search(r"::\s+(OK|PART|BLK|ERR)\b", capsule)
    return match.group(1) if match else "PART"


def _ensure_exec_ref(capsule: str, task_id: int) -> str:
    if "[ref:" in capsule:
        return capsule
    return capsule.rstrip() + f" [ref:exec/T{task_id:04d}]"


def _finalize_error(
    path: Path,
    spec: DelegateSpec,
    message: str,
    *,
    status: str = "ERR",
) -> str:
    exec_log.finalize(
        path,
        status=status,
        files_touched=[],
        validations=[],
        issues=[message],
        transcript=message,
        ended=datetime.now(timezone.utc),
    )
    return f"{spec.tier} {spec.action} {spec.target} :: {status} {message[:72]} [ref:exec/T{spec.id:04d}]"


def _blocked_capsule(spec: DelegateSpec, message: str) -> str:
    return f"{spec.tier} {spec.action} {spec.target} :: BLK {message[:72]} [ref:exec/T{spec.id:04d}]"


def _transcript(
    parts: list[str],
    user_message: str,
    stdout: str,
    stderr: str,
    returncode: int,
) -> str:
    return (
        f"$ {' '.join(parts)}\n"
        f"returncode={returncode}\n\n"
        "## User message\n\n"
        f"{user_message}\n\n"
        "## STDOUT\n\n"
        f"{stdout}\n\n"
        "## STDERR\n\n"
        f"{stderr}\n"
    )


def _short_tier(tier: str) -> str:
    normalized = tier.lower()
    if normalized == "bronze":
        return "brz"
    if normalized == "silver":
        return "slv"
    if normalized == "diamond":
        return "dia"
    if normalized == "gold":
        return "gld"
    return normalized


def _config_tier(tier: str) -> str:
    return TIER_ALIASES.get(tier.lower(), tier.lower())


def _model_from_command(command: str) -> str | None:
    tokens = command.split()
    for i, token in enumerate(tokens):
        if token in {"--model", "-m"} and i + 1 < len(tokens):
            return tokens[i + 1]
        if token.startswith("--model="):
            return token.split("=", 1)[1]
    return None
