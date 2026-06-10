from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import importlib.util
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import agents as agents_mod
from ..codec.cipher import decode as cipher_decode
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
    "dia": "silver",      # legacy alias from the old diamond/code tier
    "diamond": "silver",  # legacy alias from the old diamond/code tier
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
    return [
        d["capsule"]
        for d in run_all_detailed(
            delegate_lines,
            burnless_root=burnless_root,
            project_root=project_root,
            config=config,
        )
    ]


def run_all_detailed(
    delegate_lines: list[str],
    *,
    burnless_root: Path,
    project_root: Path,
    config: dict,
) -> list[dict]:
    details: list[dict] = []
    chain: list[str] = []
    for spec in parse_delegates(delegate_lines, burnless_root):
        u: dict = {}
        capsule_line = run_delegate(
            spec,
            burnless_root=burnless_root,
            project_root=project_root,
            config=config,
            chain=chain,
            usage_out=u,
        )
        details.append({"capsule": capsule_line, "usage": u, "status": _capsule_status(capsule_line)})
        if _capsule_status(capsule_line) == "OK":
            did = f"d{spec.id}"
            _write_dispatcher_capsule(burnless_root, did, spec, capsule_line)
            chain = [did]
    return details


def run_delegate(
    spec: DelegateSpec,
    *,
    burnless_root: Path,
    project_root: Path,
    config: dict,
    chain: list[str] | None = None,
    usage_out: dict | None = None,
) -> str:
    maestro_tier = _config_tier(spec.tier)
    compression_mode = ((config.get("compression") or {}).get("mode") or "balanced").lower()
    final_tier, modulation_reason = modulate_by_compression(maestro_tier, "", compression_mode)
    if modulation_reason:
        print(f"  · {modulation_reason}")
    if final_tier != maestro_tier or compression_mode != "balanced":
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
    from ..cli import _with_runtime_context  # local import; cli imports dispatcher lazily
    user_message = _with_runtime_context(
        user_message,
        project_root=project_root,
        burnless_root=burnless_root,
        chain=chain or None,
    )
    prompt_payload = _worker_system_prompt_payload(project_root)
    system_prompt = prompt_payload["prompt"]

    # H1: pre_worker_prompt — plugins may transform prompt and system_prompt
    from .. import plugin_loader as _pl
    _plugins = _pl.load_plugins(Path.home() / ".burnless")
    _h1 = _pl.call_all_plugins(
        _plugins, "pre_worker_prompt",
        {"hook": "pre_worker_prompt", "spec": {"id": spec.id, "tier": spec.tier, "action": spec.action, "target": spec.target}, "prompt": user_message, "system_prompt": system_prompt},
    )
    if _h1:
        user_message = _h1.get("prompt") or user_message
        system_prompt = _h1.get("system_prompt") or system_prompt

    run_parts, stdin = _inject_system_prompt(parts, system_prompt, user_message)

    # H7: worker_invoke_override — plugin may short-circuit subprocess.run
    _h7 = _pl.call_all_plugins(
        _plugins, "worker_invoke_override",
        {"hook": "worker_invoke_override", "spec": {"id": spec.id, "tier": spec.tier, "action": spec.action, "target": spec.target}, "prompt": user_message, "system_prompt": system_prompt},
    )
    _override_capsule = _h7.get("capsule") if _h7 else None
    if _override_capsule:
        capsule = str(_override_capsule)
        status = _capsule_status(capsule) if capsule else "OK"
        transcript = _transcript(run_parts, user_message, capsule, "", 0)
        exec_log.finalize(
            log_path,
            status=status,
            files_touched=[],
            validations=[],
            issues=[],
            transcript=transcript + "\n<plugin-overridden>",
            ended=datetime.now(timezone.utc),
        )
        return _ensure_exec_ref(capsule, spec.id)

    result = agents_mod.run(
        agent_cfg,
        stdin,
        timeout=int(config.get("maestro", {}).get("worker_timeout_s", 900)),
        cwd=project_root,
        tier=tier_key,
    )
    if result.get("timed_out") or result.get("interrupted"):
        return _finalize_error(
            log_path,
            spec,
            f"worker timed out after {config.get('maestro', {}).get('worker_timeout_s', 900)}s",
            status="ERR",
        )
    if usage_out is not None:
        usage_out.update(result.get("usage") or {})
    stdout = result["stdout"] or ""
    stderr = result["stderr"] or ""
    returncode = result["returncode"]

    # H2: post_worker_output — plugins may transform capsule/stdout
    _h2 = _pl.call_all_plugins(
        _plugins, "post_worker_output",
        {"hook": "post_worker_output", "spec": {"id": spec.id, "tier": spec.tier, "action": spec.action, "target": spec.target}, "stdout": stdout, "stderr": stderr, "capsule": _last_capsule(stdout) or ""},
    )
    if _h2:
        if "stdout" in _h2:
            stdout = str(_h2["stdout"])
        if "capsule" in _h2 and _h2["capsule"]:
            _injected_capsule = str(_h2["capsule"])
            stdout = stdout + "\n" + _injected_capsule if stdout else _injected_capsule

    capsule = _last_capsule(stdout)
    status = _capsule_status(capsule) if capsule else ("ERR" if returncode else "PART")
    if not capsule:
        capsule = (
            f"{spec.tier} {spec.action} {spec.target} :: {status} "
            f"missing worker capsule [ref:exec/T{spec.id:04d}]"
        )
    transcript = _transcript(result.get("command") or run_parts, user_message, stdout, stderr, returncode)
    exec_log.finalize(
        log_path,
        status=status,
        files_touched=[],
        validations=[],
        issues=[] if status == "OK" else [f"returncode={returncode}"],
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
    return _worker_system_prompt_payload(project_root)["prompt"]


def _worker_system_prompt_payload(project_root: Path) -> dict[str, str | dict[str, str]]:
    cloud_emulator = Path.home() / ".burnless" / "cloud_emulator.py"
    if cloud_emulator.exists():
        prompt_payload = _load_cloud_emulator_prompt_payload(cloud_emulator, project_root)
        if prompt_payload:
            return prompt_payload

    role_path = project_root / "_design" / "maestro_v1" / "worker_role.md"
    role_text = role_path.read_text(encoding="utf-8")
    glossary = load_glossary(project_root)
    return {
        "prompt": glossary + "\n\n---\n\n" + role_text,
        "session_env": {},
    }


def _load_cloud_emulator_prompt(module_path: Path, project_root: Path) -> str | None:
    payload = _load_cloud_emulator_prompt_payload(module_path, project_root)
    if not payload:
        return None
    return str(payload["prompt"])


def _load_cloud_emulator_prompt_payload(
    module_path: Path, project_root: Path
) -> dict[str, str | dict[str, str]] | None:
    spec = importlib.util.spec_from_file_location("burnless_cloud_emulator", module_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    previous_project_root = os.environ.get("BURNLESS_PROJECT_ROOT")
    previous_session_id = os.environ.get("BURNLESS_SESSION_ID")
    session_id = secrets.token_hex(16)
    os.environ["BURNLESS_PROJECT_ROOT"] = str(project_root)
    os.environ["BURNLESS_SESSION_ID"] = session_id
    try:
        spec.loader.exec_module(module)
        emulator_cls = getattr(module, "CloudEmulator", None)
        if emulator_cls is None:
            return None
        emulator = emulator_cls()
        payload = emulator.fetch_system_prompt()
        if not isinstance(payload, Mapping):
            return None
        wrapper = payload.get("plaintext_wrapper")
        ciphertext_b64 = payload.get("ciphertext_block")
        key_value = payload.get("key_hex")
        if (
            not isinstance(wrapper, str)
            or not isinstance(ciphertext_b64, str)
            or not isinstance(key_value, str)
        ):
            return None
        return {
            "prompt": cipher_decode(ciphertext_b64, key_value),
            "session_env": {
                "BURNLESS_SESSION_KEY_HEX": key_value,
                "BURNLESS_SESSION_CIPHERTEXT_B64": ciphertext_b64,
                "BURNLESS_SESSION_PLAINTEXT_WRAPPER": wrapper,
                "BURNLESS_SESSION_ID": session_id,
            },
        }
    finally:
        if previous_project_root is None:
            os.environ.pop("BURNLESS_PROJECT_ROOT", None)
        else:
            os.environ["BURNLESS_PROJECT_ROOT"] = previous_project_root
        if previous_session_id is None:
            os.environ.pop("BURNLESS_SESSION_ID", None)
        else:
            os.environ["BURNLESS_SESSION_ID"] = previous_session_id


def _last_capsule(stdout: str) -> str | None:
    capsule = None
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")
    # Stream-json path: extract capsule from {type: "result"} events
    for raw_line in stdout.splitlines():
        try:
            obj = json.loads(raw_line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            result_text = obj.get("result") or ""
            for inner in result_text.splitlines():
                inner = ansi_re.sub("", inner).strip()
                if CAPSULE_RE.match(inner):
                    capsule = inner
    if capsule:
        return capsule
    # Plain-text fallback for non-stream-json workers
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


def _write_dispatcher_capsule(
    burnless_root: Path, did: str, spec: DelegateSpec, capsule_line: str
) -> None:
    """Write a minimal capsule JSON so the next delegation can reference it via manifest."""
    cap_path = burnless_root / "capsules" / f"{did}.json"
    if cap_path.exists():
        return
    cap_path.parent.mkdir(parents=True, exist_ok=True)
    cap_path.write_text(
        json.dumps(
            {
                "id": did,
                "status": _capsule_status(capsule_line),
                "objective": f"{spec.action} {spec.target}",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


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
        return "slv"
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
