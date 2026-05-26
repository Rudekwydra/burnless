from __future__ import annotations
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from datetime import datetime, timezone

from . import __version__, TAGLINE
from . import config as config_mod
from . import state as state_mod
from . import metrics as metrics_mod
from . import paths as paths_mod
from . import routing as routing_mod
from . import agents as agents_mod
from . import delegations as deleg_mod
from . import compression as compression_mod
from . import lifetime as lifetime_mod
from . import claude_integration
from . import provider_autodetect
from . import brain_adapters
from . import dashboard
from . import live_runner
from .estimator import estimate_tokens
from .codec.decoder import normalize_worker_envelope
from .cmd_wrapper import run_and_capsule
from . import pipeline_state as pipeline_state_mod
from .report_kind import (
    infer_kind_hint as _infer_kind_hint,
    normalize_report_kind as _normalize_report_kind,
)
from . import init_claude_code as _init_claude_code_mod

from .delegation_parse import (
    parse_chain_from_delegation as _parse_chain_from_delegation,
    parse_tier_from_delegation as _parse_tier_from_delegation,
    parse_created_at_from_delegation as _parse_created_at_from_delegation,
    parse_goal_from_delegation as _parse_goal_from_delegation,
    extract_test_status as _extract_test_status,
)


DEFAULT_MAX_TOKENS = 4096

MAESTRO_TIER_MODEL = {
    "gold": "claude-opus-4-7",
    "silver": "claude-sonnet-4-6",
    "bronze": "claude-haiku-4-5-20251001",
}
ANTHROPIC_ENV_PATHS = (
    Path.home() / ".config" / "burnless" / "anthropic.env",
)


def _load_anthropic_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    for env_path in ANTHROPIC_ENV_PATHS:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    os.environ["ANTHROPIC_API_KEY"] = key
                    return key
    return None


def _tier_has_multiple_providers(agent_cfg: dict) -> bool:
    providers = agent_cfg.get("providers")
    return isinstance(providers, list) and len([p for p in providers if isinstance(p, dict)]) > 1


def _select_provider_cfg(agent_cfg: dict, *, tier: str) -> tuple[dict, list[dict]]:
    ranked = agents_mod.rank_providers(agent_cfg, tier=tier)
    if not ranked:
        return agent_cfg, []
    return ranked[0]["cfg"], ranked


def _record_provider_attempt(tier: str, provider_cfg: dict, result: dict) -> None:
    success = int(result.get("returncode") or 0) == 0 and not bool(result.get("timed_out")) and not bool(result.get("stale"))
    agents_mod.record_provider_result(
        tier=tier,
        provider_cfg=provider_cfg,
        success=success,
        latency_s=float(result.get("duration_s") or 0.0),
        error_at=datetime.now(timezone.utc).isoformat() if not success else None,
    )


def _run_with_maestro(
    p: dict[str, Path],
    *,
    did: str,
    tier: str,
    agent_cfg: dict,
    prompt: str,
    log_path: Path,
) -> dict | None:
    """Execute the delegation through MaestroSession (cache-hot persistent context).
    Returns a dict shaped like agents.run() output, or None if Maestro is unavailable.
    """
    api_key = _load_anthropic_key()
    if not api_key:
        return None
    try:
        import anthropic  # noqa: F401
        from . import maestro_legacy as maestro_mod
    except ImportError:
        return None

    state = state_mod.load(p["state"])
    project = state.get("project", "Project")
    plan = state.get("plan") or ""
    session_path = p["root"] / "maestro_session.jsonl"
    model = MAESTRO_TIER_MODEL.get(tier, MAESTRO_TIER_MODEL["gold"])

    started = datetime.now(timezone.utc)
    try:
        import anthropic as anthropic_mod
        client = anthropic_mod.Anthropic(api_key=api_key)
        session = maestro_mod.MaestroSession(
            path=session_path,
            system=(
                f"You are the Burnless executor for project '{project}'. "
                f"Tier: {tier}/{agent_cfg.get('name')}. "
                "Be concise, cite identifiers, finish with a short final-JSON block "
                "if the delegation requested one."
            ),
            plan=plan,
            client=client,
            main_model=model,
            cache_policy=(config_mod.load(p["config"]).get("cache_policy") or {}),
        )
        text, usage = session.run(prompt, model=model, max_tokens=2048)
    except Exception as e:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"# backend: maestro\n# tier: {tier}\n# model: {model}\n"
            f"# error: {e}\n\n--- ERROR ---\n{e}\n",
            encoding="utf-8",
        )
        return {
            "agent": agent_cfg.get("name"),
            "command": ["maestro", model],
            "stdout": "",
            "stderr": f"maestro error: {e}",
            "returncode": 1,
            "started_at": started.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": 0.0,
            "interrupted": False,
            "_maestro_error": str(e),
        }
    ended = datetime.now(timezone.utc)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "# backend: maestro\n"
        f"# tier: {tier}\n# model: {model}\n"
        f"# input_tokens: {usage.input_tokens}\n"
        f"# output_tokens: {usage.output_tokens}\n"
        f"# cache_creation_input_tokens: {usage.cache_creation_input_tokens}\n"
        f"# cache_read_input_tokens: {usage.cache_read_input_tokens}\n"
        f"# session: {session_path}\n\n"
        "--- ASSISTANT ---\n"
        f"{text}\n",
        encoding="utf-8",
    )
    return {
        "agent": agent_cfg.get("name"),
        "command": ["maestro", model],
        "stdout": text,
        "stderr": "",
        "returncode": 0,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_s": (ended - started).total_seconds(),
        "interrupted": False,
        "_maestro_usage": usage.to_dict(),
        "_maestro_session": str(session_path),
    }


def _should_use_maestro_backend(args: argparse.Namespace, cfg: dict, tier: str) -> bool:
    if tier not in MAESTRO_TIER_MODEL:
        return False
    if getattr(args, "no_maestro", False):
        return False
    if getattr(args, "maestro", False):
        return True
    return bool(cfg.get("maestro", {}).get("run_backend", False))


def _should_use_cached_worker(args: argparse.Namespace, cfg: dict, tier: str, api_key: str | None) -> bool:
    """Use CachedWorker (API direct, explicit cache_control) instead of claude -p subprocess.

    Active only when explicitly opted in via config cache_worker.enabled=true.
    Default is False — claude -p already gets prefix-cache warmth automatically
    (Claude Code injects cache_control with ephemeral_1h TTL). CachedWorker is the
    SDK path for users on variable-cost API credits who want explicit cache_control
    tuning, or whose flow benefits from the in-process tool loop instead of a
    subprocess. On the fixed monthly plan, claude -p is usually the right default.
    Enable in .burnless/config.yaml:
        cache_worker:
          enabled: true
    """
    from . import cached_worker as _cw
    if not _cw.is_available(api_key):
        return False
    if tier not in {"silver", "gold", "diamond"}:
        return False
    if getattr(args, "no_cache_worker", False):
        return False
    if getattr(args, "no_maestro", False):
        return False
    cw_cfg = cfg.get("cache_worker", {})
    # Opt-in only — default off to avoid draining API credits unintentionally
    if cw_cfg.get("enabled") is not True:
        return False
    return True


_QTP_F_FIXED_SUFFIX = (
    "\n## Output contract\n\n"
    "Worker emits a JSON block with: status (OK|PART|ERR|BLK), kind "
    "(execution|thought), summary, files_touched (absolute or cwd-relative paths), "
    "validated (\"name N bytes\" entries optional), evidence (concrete commands/checks), "
    "issues (list), next.\n"
)

_TELEGRAPHIC_OUTPUT_HINT = (
    "\n## Output style — telegraphic\n\n"
    "Responda em estilo telegráfico: sem fillers, sem prosa expansiva, abreviações curtas.\n"
    "Abreviações comuns: imp=implementar, val=validar, cfg=configuração, doc=documentação, "
    "auth=autenticação, repo=repositório, dir=diretório, arq=arquivo, ||=em paralelo.\n\n"
    "Estrutura obrigatória da saída textual (separada do JSON envelope):\n"
    "1. Header em uma linha: `<tier> :: <status> <action> <files/refs>` (status: OK|PART|ERR|BLK)\n"
    "2. Evidence — comandos rodados + outputs LITERAIS (NUNCA abreviar evidence)\n"
    "3. Relatório breve (1-2 parágrafos): decisões não óbvias, gaps detectados\n\n"
    "Regra dura: evidence, file_paths, command outputs e validated NUNCA são telegrafados — "
    "auditor precisa do literal. Só a prosa narrativa do relatório é telegráfica.\n"
    "O JSON envelope (status, kind, summary, files_touched, validated, evidence, issues, next) "
    "permanece obrigatório.\n"
)


def _extract_model(cmd_str: str, provider: str) -> str:
    """Extract model name from agent command string."""
    if provider == "anthropic":
        m = re.search(r"--model\s+(\S+)", cmd_str)
    elif provider == "codex":
        m = re.search(r"-m\s+(\S+)", cmd_str)
        if not m:
            return "gpt-5.2"
    else:
        return ""
    return m.group(1) if m else ""


def _build_cacheable_runtime_prefix(project_root: Path, burnless_root: Path) -> str:
    """QTP-F: stable prefix that doesn't change between sibling delegations.

    Putting fixed context BEFORE the variable task description maximizes
    prompt-cache hit rate (Anthropic ephemeral_1h TTL). Subsequent
    delegations in the same project share this prefix verbatim.
    """
    memory_index = burnless_root / "memories" / "index.json"
    memory_hint = (
        f"- Burnless memory index: {memory_index}\n"
        if memory_index.exists()
        else (
            "- Burnless memory index: not created yet. If the task asks about "
            "memory/anotacoes, search common local AI memory folders when your "
            "tools allow it: ~/.claude/projects, ~/.claude/memory, ~/.codex, "
            "~/.config/claude, ~/Documents/AI, ~/Documents/notes, ~/notes.\n"
        )
    )
    return (
        "## Burnless Runtime Context\n\n"
        f"- Working directory for this Worker: {project_root}\n"
        f"- Burnless state directory: {burnless_root}\n"
        f"{memory_hint}"
        "- If the task includes an absolute or relative path, inspect that path directly.\n"
        "- If the task asks to find a repository and no path is provided, search likely "
        "project roots under the working directory, ~/antigravity, ~/projects, and ~/Projects "
        "before returning BLK.\n"
        "- Do not return BLK solely because the original user phrased the request conversationally; "
        "use the available CLI/filesystem tools first.\n"
    )


def _with_runtime_context(
    prompt: str,
    *,
    project_root: Path,
    burnless_root: Path,
    chain: list[str] | None = None,
    cache_prefix: bool | None = None,
) -> str:
    """Compose worker prompt with runtime context.

    QTP-F: when cache_prefix=True (or config.cache_prefix.enabled),
    runtime context goes BEFORE the task (cacheable prefix structure).
    When False (default for backwards compat), context goes after the
    task as in v0.7.0 and earlier.
    """
    runtime = _build_cacheable_runtime_prefix(project_root, burnless_root)

    chain_manifest = ""
    if chain:
        valid: list[str] = []
        for did in chain:
            cap_path = burnless_root / "capsules" / f"{did}.json"
            if cap_path.exists():
                valid.append(did)
            else:
                print(
                    f"[lazy manifest] capsule {did} not found at {cap_path}, omitting",
                    file=sys.stderr,
                )
        if valid:
            lines = ["## Lazy Context Manifest", "- Capsules disponíveis (chain):"]
            for i, did in enumerate(valid):
                label = "predecessor direto" if i == 0 else "irmão"
                lines.append(f"  - .burnless/capsules/{did}.json — {label}")
            lines.append("- Delegations referenciadas:")
            lines.append(f"  - .burnless/delegations/{valid[0]}.md")
            lines.append("- Para ler: use sua tool de leitura (Read/cat). Tudo está no cwd.")
            chain_manifest = "\n".join(lines) + "\n"

    if cache_prefix:
        # QTP-F layout: [FIXED PREFIX] [TASK delta] [chain manifest] [FIXED SUFFIX]
        parts = [runtime.rstrip(), "", prompt.rstrip()]
        if chain_manifest:
            parts.extend(["", chain_manifest.rstrip()])
        parts.extend(["", _QTP_F_FIXED_SUFFIX.rstrip(), _TELEGRAPHIC_OUTPUT_HINT.rstrip(), ""])
        return "\n".join(parts)

    # Legacy layout (pre-QTP-F): task first, runtime context after
    result = f"{prompt.rstrip()}\n\n{runtime}"
    if chain_manifest:
        result = result.rstrip() + "\n" + chain_manifest
    result = result.rstrip() + "\n" + _TELEGRAPHIC_OUTPUT_HINT
    return result




def cmd_rtk(args: argparse.Namespace) -> int:
    """Toggle the RTK wrapper in the current project's .burnless/config.yaml.

    RTK (https://www.rtk-ai.app/) is a CLI proxy that compresses git diff /
    tool output before it hits the LLM, saving tokens on dev-heavy sessions.
    Fully opt-in: burnless works without it; turning it on prefixes `rtk` to
    every tier command so the worker subprocess passes through it.
    """
    import yaml
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg_path = p["config"]
    if not cfg_path.exists():
        print(f"burnless: no config at {cfg_path}. Run `burnless init` first.", file=sys.stderr)
        return 1
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    def _has_rtk(command: str) -> bool:
        return any(
            tok == "rtk" or Path(tok).name in ("rtk", "rtk.exe")
            for tok in shlex.split(command)
        )

    def _strip_rtk(command: str) -> str:
        return shlex.join(
            tok for tok in shlex.split(command)
            if not (tok == "rtk" or Path(tok).name in ("rtk", "rtk.exe"))
        )

    def _prefix_rtk(command: str) -> str:
        if _has_rtk(command):
            return command
        return "rtk " + command

    if args.action == "status":
        try:
            from . import rtk_loader
            resolved = rtk_loader.resolve_rtk()
        except Exception as e:
            resolved = f"(unavailable: {e})"
        enabled_tiers: list[str] = []
        for tier, agent in (cfg.get("agents") or {}).items():
            if _has_rtk(agent.get("command", "")):
                enabled_tiers.append(tier)
        print(f"RTK binary: {resolved}")
        print(f"RTK wrapping: {', '.join(enabled_tiers) if enabled_tiers else '(none)'}")
        return 0

    changed = False
    for tier, agent in (cfg.get("agents") or {}).items():
        cmd = agent.get("command", "")
        if not cmd:
            continue
        new = _prefix_rtk(cmd) if args.action == "enable" else _strip_rtk(cmd)
        if new != cmd:
            agent["command"] = new
            changed = True
        for provider in agent.get("providers", []) or []:
            pcmd = provider.get("command", "")
            if not pcmd:
                continue
            pnew = _prefix_rtk(pcmd) if args.action == "enable" else _strip_rtk(pcmd)
            if pnew != pcmd:
                provider["command"] = pnew
                changed = True

    if not changed:
        print(f"RTK already {'enabled' if args.action == 'enable' else 'disabled'} in {cfg_path.name}.")
        return 0
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    print(f"RTK {args.action}d in {cfg_path}.")
    if args.action == "enable":
        try:
            from . import rtk_loader
            print(f"  binary: {rtk_loader.resolve_rtk()}")
        except Exception as e:
            print(f"  warning: rtk binary not yet available — {e}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    if getattr(args, "claude_code", False):
        return _init_claude_code_mod.run(args)
    cwd = Path.cwd()
    root = paths_mod.root(cwd)
    p = paths_mod.paths_for(root)
    if root.exists() and not args.force:
        print(f"✓ Already initialized at {root}")
        print("  Likely created by `burnless setup` (which already runs init).")
        print("  Try `burnless` to enter the shell, or `--force` to re-init from scratch.")
        return 0
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
        p[key].mkdir(parents=True, exist_ok=True)
    detected = provider_autodetect.detect_providers()
    agents_override = provider_autodetect.build_agents(detected)
    config_mod.write_default(p["config"], agents_override=agents_override)
    print(provider_autodetect.describe(detected))
    cfg = config_mod.load(p["config"])
    initial_state = dict(state_mod.DEFAULT_STATE)
    initial_state["project"] = args.project or cwd.name
    state_mod.save(p["state"], initial_state)
    metrics_mod.save(p["metrics"], metrics_mod._fresh())
    lifetime_mod.bump(project_root=cwd)
    if getattr(args, "with_claude_md", False) and not getattr(args, "no_claude_md", False):
        try:
            from . import __version__ as _v
        except ImportError:
            _v = "0.7.4"
        claude_md = cwd / "CLAUDE.md"
        action = claude_integration.write_or_update(
            claude_md, version=_v, project_name=initial_state["project"]
        )
        print(f"CLAUDE.md: {action} burnless block at {claude_md}")
    else:
        print("CLAUDE.md: skipped (default — use --with-claude-md to opt in)")
    p["maestro"].write_text(
        f"# Maestro — {initial_state['project']}\n\n_No plan yet. Run `burnless plan \"...\"`._\n",
        encoding="utf-8",
    )
    p["history"].write_text("# Burnless Chat History\n", encoding="utf-8")
    print(f"Burnless initialized at {root}")
    print(f"Project: {initial_state['project']}")
    print(f"\n{TAGLINE}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    plan_text = args.text
    state = state_mod.load(p["state"])
    state["plan"] = plan_text
    state_mod.save(p["state"], state)
    p["maestro"].write_text(
        f"# Maestro — {state.get('project', 'Project')}\n\n## Plan\n\n{plan_text}\n",
        encoding="utf-8",
    )
    cfg = config_mod.load(p["config"])
    # Reusing the plan as state instead of re-briefing the agent counts as
    # repeated_context_avoided. Estimate by the plan size.
    saved = estimate_tokens(plan_text)
    if saved > 0:
        _record_and_bump(
            p,
            source="repeated_context_avoided",
            amount=saved,
            reason="plan stored as compact state instead of re-briefing per delegation",
            usd_per_million=cfg["metrics"]["expensive_model_usd_per_million"],
        )
    print(f"Plan saved. ({saved} tokens routed to compact state)")
    return 0


TIER_RANK = {"bronze": 1, "silver": 2, "gold": 3, "diamond": 2}


def _hardcore_blocked(
    cfg: dict,
    text: str,
    tier_override: str | None,
    args: argparse.Namespace,
) -> tuple[bool, str, str]:
    """Return (blocked, natural_tier, matched_kw). Block when override upgrades vs route."""
    if not tier_override:
        return False, "", ""
    enabled = cfg.get("routing", {}).get("hardcore_filter", False) or os.environ.get(
        "BURNLESS_HARDCORE"
    ) in ("1", "true", "yes")
    if not enabled or getattr(args, "force", False):
        return False, "", ""
    natural_tier, kw = routing_mod.route(text, cfg["routing"])
    if TIER_RANK.get(tier_override, 0) > TIER_RANK.get(natural_tier, 0):
        return True, natural_tier, kw or "default"
    return False, natural_tier, kw or ""


def cmd_delegate(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    metrics = metrics_mod.load(p["metrics"])
    text = args.text
    tier_override = args.tier
    allow_rel = getattr(args, "allow_relative_paths", False)
    require_abs = cfg.get("validation", {}).get("require_absolute_paths", True)
    if not allow_rel and require_abs:
        from . import spec_validator
        sv = spec_validator.validate_spec_paths(text)
        if not sv.ok:
            lang = cfg.get("language", "pt-BR")
            print(spec_validator.format_rejection(sv, root.parent, lang), file=sys.stderr)
            return 6

    is_blocked, natural_tier, matched_kw = _hardcore_blocked(cfg, text, tier_override, args)
    if is_blocked:
        lang = cfg.get("language", "pt-BR")
        if lang.startswith("pt"):
            print(
                f"\n🚨 burnless hardcore: rota natural detectou {natural_tier} ({matched_kw}).\n"
                f"   override pra {tier_override} bloqueado.\n"
                f"   manual override: --force  ou  unset routing.hardcore_filter\n"
            )
        else:
            print(
                f"\n🚨 burnless hardcore: natural route resolved to {natural_tier} ({matched_kw}).\n"
                f"   override to {tier_override} blocked.\n"
                f"   manual override: --force  or  unset routing.hardcore_filter\n"
            )
        return 5

    if tier_override:
        tier, kw = tier_override, "manual"
        modulation_reason = ""
    else:
        tier, kw = routing_mod.route(text, cfg["routing"])
        comp_mode = cfg.get("compression", {}).get("mode", "balanced")
        tier, modulation_reason = routing_mod.modulate_by_compression(tier, kw, comp_mode)
    if tier not in cfg["agents"]:
        fallback = "gold" if tier == "diamond" else "silver"
        print(
            f"burnless: tier '{tier}' not configured in this project — falling back to {fallback}.",
            file=sys.stderr,
        )
        print(f"  Add agents.{tier} to .burnless/config.yaml to use this tier.", file=sys.stderr)
        tier = fallback
    agent_cfg = cfg["agents"][tier]

    chain = [x.strip() for x in args.chain.split(",") if x.strip()] if args.chain else []

    did = state_mod.alloc_delegation_id(p["state"])
    # Expose the freshly-allocated id back to the caller (e.g. cmd_do) without
    # relying on state.last_delegation, which is racy under parallel dispatch.
    setattr(args, "_allocated_did", did)
    goal = args.goal or text
    # Downgrade H2 headers in spec text so they don't collide with the
    # delegation template's own ## Goal / ## Task / ## Constraints sections.
    goal = re.sub(r"^##\s", "### ", goal, flags=re.MULTILINE)
    text = re.sub(r"^##\s", "### ", text, flags=re.MULTILINE)
    success = args.success or "task completed; final JSON block emitted as required."
    kind_hint = _infer_kind_hint(text)
    chat_mode = bool(getattr(args, "chat", False))
    if chat_mode:
        body = deleg_mod.render_maestro_chat(
            delegation_id=did,
            task=text,
            agent_name=agent_cfg["name"],
            tier=tier,
        )
    else:
        body = deleg_mod.render_delegation(
            delegation_id=did,
            goal=goal,
            task=text,
            success=success,
            kind_hint=kind_hint,
            agent_name=agent_cfg["name"],
            tier=tier,
            routed_by=kw,
        )
    deleg_path = p["delegations"] / f"{did}.md"
    if chain:
        header = f"---\nchain: [{', '.join(chain)}]\n---\n"
        deleg_mod.write_delegation(deleg_path, header + body)
    else:
        deleg_mod.write_delegation(deleg_path, body)
    # Marker file so the shell renderer knows to display the natural-text
    # response rather than parsing for the JSON schema (which won't exist).
    if chat_mode:
        runs_dir = p["root"] / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / f"{did}.chat").write_text("", encoding="utf-8")
    def _set_last(st):
        st["last_delegation"] = did
    state_mod.update_locked(p["state"], _set_last)

    # Routing a code task to bronze instead of opus avoids expensive context.
    # Count this only when we *de-escalated* from default gold to a cheaper tier,
    # which is the most defensible "expensive_model_avoided" signal.
    if tier in ("bronze", "silver"):
        # estimate by length of the prompt that won't be sent to opus
        avoided = estimate_tokens(body)
        _record_and_bump(
            p,
            source="expensive_model_avoided",
            amount=avoided,
            reason=f"routed to {tier}/{agent_cfg['name']} instead of gold/opus",
            delegation_id=did,
            extra={"matched_keyword": kw},
            usd_per_million=cfg["metrics"]["expensive_model_usd_per_million"],
        )

    print(f"Delegation {did} → {tier}/{agent_cfg['name']}  (matched: {kw or 'default'})")
    if modulation_reason:
        print(f"  · {modulation_reason}")
    print(f"  {deleg_path}")
    print(f"\nRun with: burnless run {did}")
    return 0



def cmd_run(args: argparse.Namespace) -> int:
    """QTP-C wrapper: applies parallel-launch jitter + in-flight lock around _cmd_run_body."""
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    from . import parallel_jitter as _pj
    _pj_cfg = cfg.get("parallel_jitter", {})
    _pj_enabled = bool(_pj_cfg.get("enabled", True))
    _pj_min = float(_pj_cfg.get("min_s", 0.5))
    _pj_max = float(_pj_cfg.get("max_s", 2.5))
    if _pj_enabled:
        _delay = _pj.maybe_jitter(root, min_s=_pj_min, max_s=_pj_max, enabled=True)
        if _delay > 0:
            print(
                f"[jitter] {_delay:.1f}s before launch (other workers in flight)",
                file=sys.stderr,
            )
        with _pj.in_flight(root, args.id):
            return _cmd_run_body(args)
    return _cmd_run_body(args)


def _cmd_run_body(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    state = state_mod.load(p["state"])
    metrics = metrics_mod.load(p["metrics"])
    metrics_mod.bump_legacy_counter(p["metrics"], "legacy_run_calls")
    did = args.id
    deleg_path = p["delegations"] / f"{did}.md"
    if not deleg_path.exists():
        print(f"burnless: delegation {did} not found at {deleg_path}", file=sys.stderr)
        return 2
    deleg_text = deleg_path.read_text(encoding="utf-8")
    chain = _parse_chain_from_delegation(deleg_text)
    cache_prefix = bool((cfg.get("cache_prefix") or {}).get("enabled", False))
    prompt = _with_runtime_context(
        deleg_text,
        project_root=root.parent,
        burnless_root=root,
        chain=chain,
        cache_prefix=cache_prefix,
    )

    # which tier did we pick at delegate time?
    # cheap parse: look at "agent:" line in the markdown
    tier = _parse_tier_from_delegation(prompt) or "bronze"
    prompt = agents_mod.maybe_prepend_prior_decision(prompt, tier=tier)
    # NOTE: prune-by-drift was removed in the per-(provider, model) warm refactor
    # (commit fa5cbf4). Different models now live in different files, so the
    # "model drift" condition cannot arise. The agent_cfg / provider lookup
    # below is still needed by downstream code.
    agent_cfg = cfg["agents"][tier]
    selected_agent_cfg, ranked_providers = _select_provider_cfg(agent_cfg, tier=tier)
    selected_provider = selected_agent_cfg.get("provider") or selected_agent_cfg.get("name")
    provider_name = selected_agent_cfg.get("provider") or ""

    # Codex warm brief injection is re-wired in phase 3; for now leave empty.
    warm_codex_brief = ""
    warm_codex_flags: list[str] = []

    if args.dry_run:
        print(f"[dry-run] would run: {' '.join(agents_mod.resolve_command(selected_agent_cfg))}")
        if selected_provider:
            print(f"[dry-run] selected provider: {selected_provider}")
        print(f"[dry-run] prompt size: {len(prompt)} chars (~{estimate_tokens(prompt)} tokens)")
        return 0

    if not agents_mod.is_available(selected_agent_cfg):
        print(
            f"burnless: agent binary not in PATH for tier {tier} ({selected_agent_cfg.get('name')}).",
            file=sys.stderr,
        )
        print(f"  configured command: {selected_agent_cfg['command']}", file=sys.stderr)
        print("  fix: install the CLI or edit .burnless/config.yaml", file=sys.stderr)
        return 3

    log_path = p["logs"] / f"{did}.log"
    bt_before = metrics_mod.load(p["metrics"])["burnless_tokens"]
    # Mode resolution: --progress > config display.progress_detail > legacy --watch/--quiet/--full
    progress_arg = getattr(args, "progress", None)
    if progress_arg:
        run_mode = progress_arg
    else:
        legacy_mode = getattr(args, "mode", None)
        if legacy_mode and legacy_mode != "plain":
            run_mode = legacy_mode
        else:
            display_cfg = cfg.get("display", {}).get("progress_detail", "brief")
            run_mode = display_cfg if display_cfg in {"minimal", "brief", "full", "watch", "quiet", "plain"} else "brief"
    from burnless.config import resolve_stale_timeout
    stale_timeout_s = resolve_stale_timeout(cfg, tier, getattr(args, "stale_timeout_s", None))

    # Persist a lightweight run snapshot before handing off to the worker.
    runs_dir = p["runs"]
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_plan = {
        "id": did,
        "tier": tier,
        "agent": selected_agent_cfg.get("name"),
        "provider": selected_provider,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "delegation": str(p["delegations"] / f"{did}.md"),
    }
    (runs_dir / f"{did}.plan.json").write_text(
        json.dumps(run_plan, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    api_key = _load_anthropic_key()
    multi_provider = _tier_has_multiple_providers(agent_cfg)
    use_maestro = (not multi_provider) and _should_use_maestro_backend(args, cfg, tier)
    use_cached_worker = (not multi_provider) and (not use_maestro) and _should_use_cached_worker(args, cfg, tier, api_key)
    result: dict | None = None
    backend_used = "subprocess"
    if use_maestro:
        result = _run_with_maestro(
            p, did=did, tier=tier, agent_cfg=selected_agent_cfg, prompt=prompt, log_path=log_path,
        )
        if result is not None:
            backend_used = "maestro"
            if sys.stdout.isatty() or getattr(args, "verbose", False):
                print(f"Running {did} with maestro/{tier} ({result['command'][1]})...")

    if result is None and use_cached_worker:
        from . import cached_worker as _cw
        model = MAESTRO_TIER_MODEL.get(tier, MAESTRO_TIER_MODEL["silver"])
        if sys.stdout.isatty() or getattr(args, "verbose", False):
            print(f"Running {did} with cached_worker/{tier} ({model})...", flush=True)
        try:
            result = _cw.run_cached_worker(
                prompt=prompt,
                model=model,
                project_root=root.parent,
                burnless_root=root,
                api_key=api_key,
                max_tokens=DEFAULT_MAX_TOKENS,
                timeout_s=stale_timeout_s,
                log_path=log_path,
                cold_cache=getattr(args, "cold_cache", False),
            )
            backend_used = "cached_worker"
        except Exception as e:
            print(f"CachedWorker failed ({e}); falling back to subprocess.", file=sys.stderr)

    if result is None:
        try:
            result_obj = live_runner.run_with_overflow_retries(
                delegation_id=did,
                tier=tier,
                agent_cfg=selected_agent_cfg,
                prompt=prompt,
                log_path=log_path,
                mode=run_mode,
                burnless_tokens=bt_before,
                timeout=args.timeout,
                stale_timeout=stale_timeout_s,
                tool_suspect_interval_s=int((cfg.get("display") or {}).get("tool_suspect_interval_s", 60)),
                tool_hard_max_s=int((cfg.get("display") or {}).get("tool_hard_max_s", 1800)),
                cwd=root.parent,
                tier_agents=cfg.get("agents", {}),
                liveness_mode=str((cfg.get("display") or {}).get("liveness_mode", "time")),
                warm_codex_brief=warm_codex_brief,
                warm_codex_flags=warm_codex_flags,
            )
            result = result_obj.to_dict()
        except Exception as e:
            print(f"Runner failed; falling back to plain runner. ({e})", file=sys.stderr)
            if sys.stdout.isatty() or getattr(args, "verbose", False):
                print(f"Running {did} with {tier}/{selected_agent_cfg['name']}...")
            result = agents_mod.run(selected_agent_cfg, prompt, timeout=args.timeout, cwd=root.parent)
            deleg_mod.write_log(log_path, result)

    if result is not None and backend_used in {"subprocess", "cached_worker"} and not result.get("provider_attempts"):
        _record_provider_attempt(tier, selected_agent_cfg, result)

    if (
        result is not None
        and ranked_providers
        and len(ranked_providers) > 1
        and agents_mod._retryable_provider_failure(result)
        and int(result.get("returncode") or 0) != 0
    ):
        fallback_cfg = ranked_providers[1]["cfg"]
        print(
            f"[provider-fallback] {did}: {selected_provider} failed, retrying with {fallback_cfg.get('provider') or fallback_cfg.get('name')}",
            file=sys.stderr,
        )
        if backend_used == "subprocess":
            fallback_result = live_runner.run_with_live_panel(
                delegation_id=did,
                tier=tier,
                agent_cfg=fallback_cfg,
                prompt=prompt,
                log_path=log_path,
                mode=run_mode,
                burnless_tokens=bt_before,
                timeout=args.timeout,
                stale_timeout=stale_timeout_s,
                tool_suspect_interval_s=int((cfg.get("display") or {}).get("tool_suspect_interval_s", 60)),
                tool_hard_max_s=int((cfg.get("display") or {}).get("tool_hard_max_s", 1800)),
                cwd=root.parent,
                append_log=True,
                liveness_mode=str((cfg.get("display") or {}).get("liveness_mode", "time")),
                warm_codex_brief=warm_codex_brief,
                warm_codex_flags=warm_codex_flags,
            ).to_dict()
        else:
            fallback_result = agents_mod.run(fallback_cfg, prompt, timeout=args.timeout, cwd=root.parent, tier=tier)
            with log_path.open("a", encoding="utf-8") as _lf:
                _lf.write("\n\n--- PROVIDER FALLBACK ATTEMPT ---\n" + fallback_result.get("stdout", "") + "\n")
        if not fallback_result.get("provider_attempts"):
            _record_provider_attempt(tier, fallback_cfg, fallback_result)
        fallback_result["provider_attempts"] = [
            {
                "provider": selected_provider,
                "returncode": result.get("returncode"),
                "timed_out": bool(result.get("timed_out")) or bool(result.get("stale")),
            },
            {
                "provider": fallback_cfg.get("provider") or fallback_cfg.get("name"),
                "returncode": fallback_result.get("returncode"),
                "timed_out": bool(fallback_result.get("timed_out")) or bool(fallback_result.get("stale")),
            },
        ]
        result = fallback_result

    # Always isolate raw log out of the main context.
    raw_size = estimate_tokens(result.get("stdout", "")) + estimate_tokens(result.get("stderr", ""))
    _record_and_bump(
        p,
        source="raw_logs_isolated",
        amount=raw_size,
        reason=f"raw stdout/stderr from {selected_agent_cfg['name']} kept out of main context",
        delegation_id=did,
        usd_per_million=cfg["metrics"]["expensive_model_usd_per_million"],
    )

    interrupted = bool(result.get("interrupted"))
    stale = bool(result.get("stale"))
    extracted_json = deleg_mod.extract_result_json(result.get("stdout", ""))
    if extracted_json is not None:
        summary = normalize_worker_envelope(extracted_json)
        summary["kind"] = _normalize_report_kind(summary.get("kind") or summary.get("report_kind") or _infer_kind_hint(prompt))
        _ev = summary.get("evidence")
        if not isinstance(_ev, list) or not _ev:
            if summary["kind"] == "thought":
                summary["status"] = str(summary.get("status") or "OK").upper()
                summary.setdefault("validated", [])
                summary.setdefault("files_touched", [])
                summary.setdefault("evidence", [])
                summary.setdefault("issues", [])
    elif backend_used == "maestro" and result["returncode"] == 0 and not interrupted:
        # Maestro mode: assistant text without explicit JSON still counts as OK.
        snippet = (result.get("stdout") or "").strip().splitlines()
        first_line = snippet[0] if snippet else ""
        summary = normalize_worker_envelope({
            "id": did,
            "status": "OK",
            "kind": _infer_kind_hint(prompt),
            "summary": first_line[:160] or "Maestro turn completed.",
            "files_touched": [],
            "validated": [],
            "issues": [],
            "next": "",
        })
    else:
        if stale:
            _status = "PART"
            _summary = f"Stale worker: no output for {stale_timeout_s}s, process killed."
            _issue = "stale_worker"
        elif interrupted:
            _status = "ERR" if result["returncode"] != 0 else "PART"
            _summary = "Worker stopped by user."
            _issue = "user_interrupted"
        else:
            # v0.8: no envelope is fine. Status from exit code; body = last line of stdout.
            _status = "OK" if result["returncode"] == 0 else "ERR"
            _stdout_lines = (result.get("stdout") or "").strip().splitlines()
            _stdout_tail = _stdout_lines[-1] if _stdout_lines else ""
            _summary = (_stdout_tail[:200] or "Worker finished.").strip()
            _issue = "" if result["returncode"] == 0 else f"returncode={result['returncode']}"
        summary = normalize_worker_envelope({
            "id": did,
            "status": _status,
            "kind": _infer_kind_hint(prompt),
            "summary": _summary,
            "files_touched": [],
            "validated": [],
            "issues": [_issue] if _issue else [],
            "next": "",
        })

    # ── BLK lazy fetch fallback (P3) ─────────────────────────────────────────
    _blk_initial = str(summary.get("status") or "").upper()
    if _blk_initial == "BLK":
        _blk_issues = summary.get("issues") or []
        _lazy_fetch_re = re.compile(r"lazy fetch failed", re.IGNORECASE)
        if any(_lazy_fetch_re.search(str(i)) for i in _blk_issues):
            print(
                f"[lazy-fallback] {did}: BLK with lazy fetch failure → retrying with full push",
                file=sys.stderr,
            )
            _inline_parts: list[str] = []
            for _iss in _blk_issues:
                _m = re.search(r"lazy fetch failed:\s*(.+)", str(_iss), re.IGNORECASE)
                if _m:
                    _failed_rel = _m.group(1).strip()
                    _cap_abs = root.parent / _failed_rel
                    if _cap_abs.exists():
                        _inline_parts.append(
                            f"## Capsule {_failed_rel} (inlined for fallback)\n"
                            f"```json\n{_cap_abs.read_text(encoding='utf-8')}\n```"
                        )
            _full_push_prompt = _with_runtime_context(
                deleg_text,
                project_root=root.parent,
                burnless_root=root,
                chain=None,
            )
            if _inline_parts:
                _full_push_prompt = _full_push_prompt.rstrip() + "\n\n" + "\n\n".join(_inline_parts) + "\n"
            _full_push_prompt = (
                _full_push_prompt.rstrip()
                + "\n\n_lazy_disabled=True: capsule context inlined above.\n"
            )
            try:
                _blk_retry = agents_mod.run(
                    agent_cfg, _full_push_prompt, timeout=args.timeout, cwd=root.parent
                )
                with log_path.open("a", encoding="utf-8") as _lf:
                    _lf.write("\n\n--- LAZY_FALLBACK ---\n" + _blk_retry.get("stdout", "") + "\n")
                _blk_rj = deleg_mod.extract_result_json(_blk_retry.get("stdout", ""))
                if _blk_rj is not None:
                    _blk_rj = normalize_worker_envelope(_blk_rj)
                    _blk_rj["kind"] = _normalize_report_kind(
                        _blk_rj.get("kind") or _blk_rj.get("report_kind") or _infer_kind_hint(prompt)
                    )
                    summary = _blk_rj
            except Exception as _blk_e:
                print(f"[lazy-fallback] {did}: retry failed ({_blk_e})", file=sys.stderr)

    # ── Bronze rescue for stale_worker (Gapless applied to rescue) ───────────
    _rescue_cfg = cfg.get("retry", {})
    _bronze_rescue_enabled = bool(_rescue_cfg.get("bronze_rescue", True))
    if _bronze_rescue_enabled and stale and "stale_worker" in (summary.get("issues") or []):
        _bronze_tier_cfg = cfg.get("agents", {}).get("bronze")
        if _bronze_tier_cfg and agents_mod.is_available(_bronze_tier_cfg):
            print(f"[bronze-rescue] {did}: stale_worker — launching deterministic check", file=sys.stderr)
            _deleg_created_at = _parse_created_at_from_delegation(deleg_text)
            _rescue_prompt = (
                f"Verifique se a delegação {did} completou seu trabalho apesar de morrer por stale_worker.\n"
                f"A delegação foi criada em: {_deleg_created_at or 'desconhecido'}.\n"
                "IMPORTANTE: só considere rescued=true se os arquivos relevantes foram criados/modificados\n"
                f"DEPOIS de {_deleg_created_at or 'a criação da delegação'} (verifique mtime via `ls -l --time-style=full-iso` ou `stat`).\n"
                "Arquivos com timestamp ANTERIOR à delegação são de runs anteriores — retorne rescued=false nesses casos.\n\n"
                "Cheque deterministicamente (sem reimplementar):\n"
                "- Timestamps dos arquivos vs data de criação da delegação acima\n"
                "- Se arquivos existem e têm conteúdo plausível (wc -l, grep para conteúdo chave mencionado na spec)\n"
                "- Se pytest passa (se disponível)\n\n"
                "ATENÇÃO: O JSON de resposta deve usar EXATAMENTE este schema (não o schema padrão de delegação):\n"
                "{\"rescued\": true|false, \"evidence\": [\"<item verificável>\", ...], \"files_found\": [\"<caminho>\", ...]}\n"
                "NÃO use campos como 'status', 'summary', 'files_touched' — use APENAS rescued, evidence, files_found.\n"
                "Se rescued=true, o sistema usa como OK capsule automaticamente.\n\n"
                f"## Delegation spec:\n{deleg_text}\n\n"
                f"## Burnless Runtime Context\n"
                f"- Working directory: {root.parent}\n"
                f"- Burnless state directory: {root}\n"
            )
            try:
                _rescue_timeout = min(int(args.timeout or 300), 300)
                _rescue_result = agents_mod.run(
                    _bronze_tier_cfg, _rescue_prompt, timeout=_rescue_timeout, cwd=root.parent
                )
                with log_path.open("a", encoding="utf-8") as _lf:
                    _lf.write("\n\n--- BRONZE_RESCUE ---\n" + _rescue_result.get("stdout", "") + "\n")
                _rescue_json = deleg_mod.extract_result_json(_rescue_result.get("stdout", ""))
                if _rescue_json is None:
                    try:
                        _stdout = _rescue_result.get("stdout", "").strip()
                        _json_start = _stdout.rfind("{")
                        _json_end = _stdout.rfind("}") + 1
                        if _json_start >= 0 and _json_end > _json_start:
                            _rescue_json = json.loads(_stdout[_json_start:_json_end])
                    except Exception:
                        _rescue_json = None
                if _rescue_json is not None and bool(_rescue_json.get("rescued")):
                    _rescue_evidence = list(_rescue_json.get("evidence") or [])
                    _rescue_files = list(_rescue_json.get("files_found") or [])
                    print(f"[bronze-rescue] {did}: rescued=True — work verified despite stale_worker", file=sys.stderr)
                    summary = normalize_worker_envelope({
                        "id": did,
                        "status": "OK",
                        "kind": _infer_kind_hint(prompt),
                        "summary": f"rescued_from_stale: work verified by bronze rescue ({len(_rescue_evidence)} evidence items)",
                        "files_touched": _rescue_files,
                        "validated": _rescue_evidence,
                        "evidence": _rescue_evidence,
                        "issues": ["rescued_from_stale"],
                        "next": "",
                        "_bronze_rescue": _rescue_json,
                    })
                    stale = False
                else:
                    print(f"[bronze-rescue] {did}: rescued=False — proceeding to retry with timeout*2", file=sys.stderr)
            except Exception as _resc_e:
                print(f"[bronze-rescue] {did}: rescue failed ({_resc_e}), falling back to retry", file=sys.stderr)

    # ── PART/ERR automatic retry loop (before audit) ─────────────────────────
    retry_cfg = cfg.get("retry", {})
    _max_attempts = int(retry_cfg.get("max_attempts", 1))
    _stale_retry_enabled = bool(retry_cfg.get("stale_worker_retry", True))
    _retry_count = 0
    _retry_status: list[str] = []

    _cur_status = str(summary.get("status") or "").upper()
    if _cur_status in ("PART", "ERR") and not interrupted:
        _summary_issues = {str(x) for x in (summary.get("issues") or [])}
        if "context_overflow_retry_exhausted" in _summary_issues:
            _do_retry = False
        else:
            _do_retry = None
        _is_stale = stale and "stale_worker" in (summary.get("issues") or [])
        _attempts_left = 1 if _is_stale else _max_attempts
        if _do_retry is None:
            _do_retry = (_is_stale and _stale_retry_enabled) or (not _is_stale and _attempts_left > 0)

        while _do_retry and _attempts_left > 0:
            _attempts_left -= 1
            _retry_status.append(_cur_status)

            _retry_prompt_text = prompt
            if _is_stale:
                _retry_timeout = min(stale_timeout_s * 2, int(args.timeout or stale_timeout_s * 2))
            else:
                _retry_timeout = int(args.timeout or 600)

            print(f"[retry] {did}: prev={_cur_status}, attempt {_retry_count + 1}", file=sys.stderr)
            try:
                _retry_res = agents_mod.run(
                    agent_cfg, _retry_prompt_text, timeout=_retry_timeout, cwd=root.parent
                )
            except Exception as _re:
                print(f"[retry] agent run failed: {_re}", file=sys.stderr)
                break

            with log_path.open("a", encoding="utf-8") as _lf:
                _lf.write(f"\n\n--- RETRY_{_retry_count + 1} ---\n" + _retry_res.get("stdout", "") + "\n")

            _rj = deleg_mod.extract_result_json(_retry_res.get("stdout", ""))
            _r_stale = bool(_retry_res.get("stale"))
            _r_interrupted = bool(_retry_res.get("interrupted"))
            if _rj is not None:
                _r_sum = normalize_worker_envelope(_rj)
                _r_sum["kind"] = _normalize_report_kind(
                    _r_sum.get("kind") or _r_sum.get("report_kind") or _infer_kind_hint(prompt)
                )
            else:
                _r_rc = _retry_res.get("returncode", 1)
                if _r_stale:
                    _r_issue = "stale_worker"
                    _r_status = "PART"
                    _r_summary = "(retry: stale worker)"
                else:
                    # v0.8: no envelope is fine. Status from exit code.
                    _r_status = "OK" if _r_rc == 0 else "ERR"
                    _r_lines = (_retry_res.get("stdout") or "").strip().splitlines()
                    _r_summary = ((_r_lines[-1] if _r_lines else "")[:200] or "Worker finished (retry).").strip()
                    _r_issue = "" if _r_rc == 0 else f"returncode={_r_rc}"
                _r_sum = normalize_worker_envelope({
                    "id": did,
                    "status": _r_status,
                    "kind": _infer_kind_hint(prompt),
                    "summary": _r_summary,
                    "files_touched": [],
                    "validated": [],
                    "issues": [_r_issue] if _r_issue else [],
                    "next": "",
                })

            _retry_count += 1
            _new_status = str(_r_sum.get("status") or "").upper()

            if _new_status == "OK":
                summary = _r_sum
                stale = _r_stale
                interrupted = _r_interrupted
                break

            _orig_issues = summary.get("issues") or []
            _r_issues = _r_sum.get("issues") or []
            summary = _r_sum
            summary["issues"] = list(dict.fromkeys(_orig_issues + _r_issues))
            _cur_status = _new_status
            stale = _r_stale
            interrupted = _r_interrupted
            _do_retry = _attempts_left > 0

    summary["retry_count"] = _retry_count
    summary["retry_status"] = _retry_status

    _worker_status = str(summary.get("status") or "?").upper()

    summary["worker_status"] = _worker_status
    summary["test_status"] = _extract_test_status(summary)
    agents_mod.remember_silver_decision(
        tier=tier,
        prompt=prompt,
        summary=summary,
        stdout=result.get("stdout", ""),
    )

    deleg_mod.write_summary(p["temp"] / f"{did}.json", summary)

    # Automatic Session Compression — generate capsule (operational memory for AI)
    raw_mode = cfg.get("compression", {}).get("mode", compression_mod.DEFAULT_MODE)
    mode = compression_mod.normalize_mode(raw_mode)
    if mode not in compression_mod.MODES:
        print(
            f"burnless: invalid compression.mode={raw_mode!r}; falling back to {compression_mod.DEFAULT_MODE}",
            file=sys.stderr,
        )
        mode = compression_mod.DEFAULT_MODE
    raw_log_text = log_path.read_text(encoding="utf-8")
    goal = _parse_goal_from_delegation(prompt) or summary.get("summary", "")
    capsule = compression_mod.compress(
        delegation_id=did,
        goal=goal,
        summary=summary,
        raw_log=raw_log_text,
        mode=mode,
    )
    capsule_path = p["capsules"] / f"{did}.json"
    compression_mod.write(capsule_path, capsule)

    savings = compression_mod.measure_savings(raw_log_text, capsule)
    capsule.tokens = savings
    compression_mod.write(capsule_path, capsule)
    if savings["saved_tokens"] > 0:
        _record_and_bump(
            p,
            source="capsule_compression",
            amount=savings["saved_tokens"],
            reason=(
                f"capsule mode={mode}: raw {savings['raw_tokens']}t → "
                f"capsule {savings['capsule_tokens']}t (×{savings['compression_ratio']})"
            ),
            delegation_id=did,
            extra={"mode": mode, "ratio": savings["compression_ratio"]},
            usd_per_million=cfg["metrics"]["expensive_model_usd_per_million"],
            capsules_delta=1,
        )
        metrics_mod.bump_legacy_counter(p["metrics"], "legacy_compress_calls")
    else:
        lifetime_mod.bump(project_root=root.parent, capsules_delta=1)

    # State carries only the capsule pointer + the next step from the capsule.
    # Raw logs and the agent's verbose stdout never reach state.json.
    state["last_delegation"] = did
    state["last_capsule"] = did
    state["last_capsule_mode"] = mode
    state["next"] = capsule.next or None
    state_mod.save(p["state"], state)

    # Short output — details via `burnless read/log/capsule/metrics`
    # Default = single-line machine-parseable status (avoids polluting maestro
    # session history). Verbose (3-line summary+reason) opt-in via --verbose
    # or auto-on for TTY humans.
    status_str = summary.get("status", "?")
    verbose = bool(getattr(args, "verbose", False)) or sys.stdout.isatty()
    if interrupted and not stale:
        if verbose:
            print("Worker stopped by user.")
        else:
            print(f"INT:{did}")
    else:
        head = f"{status_str}:{did}"
        if verbose:
            summary_text = (summary.get("summary") or "").strip()
            if summary_text:
                head = f"{head}\n{summary_text}"
            if status_str != "OK":
                feedback = str(summary.get("next") or "").strip()
                if feedback:
                    head = f"{head}\nReason: {feedback[:180]}"
        print(head)
    return 0 if status_str == "OK" else 1





def cmd_status(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    state = state_mod.load(p["state"])
    m = metrics_mod.load(p["metrics"])
    print(dashboard.render_status(state, m))
    return 0


def cmd_providers_stats(args: argparse.Namespace) -> int:
    snapshot = agents_mod.provider_health_snapshot()
    rows = agents_mod.list_provider_stats()
    last_used = snapshot.get("last_used_provider")
    if last_used:
        provider_name = last_used.get("provider") or last_used.get("name") or "-"
        print(
            f"last_used_provider={provider_name} "
            f"tier={last_used.get('tier') or '-'} "
            f"updated_at={last_used.get('updated_at') or '-'}"
        )
    if not rows:
        print("(no provider health stats)")
        return 0
    for row in rows:
        print(
            f"{row.get('key')} "
            f"success_rate={float(row.get('success_rate') or 0.0):.2f} "
            f"avg_latency={float(row.get('avg_latency') or 0.0):.2f}s "
            f"last_error_at={row.get('last_error_at') or '-'}"
        )
    return 0


def cmd_providers_reset(args: argparse.Namespace) -> int:
    cleared = agents_mod.reset_provider_health()
    print(f"cleared {cleared} provider health record(s)")
    return 0


def cmd_provider_status(args: argparse.Namespace) -> int:
    return cmd_providers_stats(args)


def cmd_provider_reset(args: argparse.Namespace) -> int:
    return cmd_providers_reset(args)


def cmd_decisions_list(args: argparse.Namespace) -> int:
    entries = agents_mod.list_decisions()
    if getattr(args, "json", False):
        print(json.dumps(entries, indent=2, ensure_ascii=False))
        return 0
    if not entries:
        print("(no cached decisions)")
        return 0
    for entry in entries:
        print(
            f"{entry.get('decision_hash')} "
            f"hits={entry.get('hits', 0)} "
            f"last_used={entry.get('last_used', '')}"
        )
        print(f"  context: {entry.get('context_summary', '')}")
        print(f"  decision: {entry.get('decision_text', '')}")
    return 0


def cmd_decisions_clear(args: argparse.Namespace) -> int:
    cleared = agents_mod.clear_decisions()
    print(f"cleared {cleared} cached decision(s)")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    if getattr(args, "metrics_cmd", None) == "desktop":
        return cmd_metrics_desktop(args)

    if getattr(args, "global_view", False):
        from pathlib import Path
        from datetime import datetime
        import json as _json
        path = Path.home() / ".burnless" / "global_metrics.jsonl"
        if not path.exists():
            print("No global events yet. Run any `burnless do/delegate/run` to populate.")
            return 0
        since = getattr(args, "since", None)
        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since)
            except Exception:
                print(f"Invalid --since format: {since}. Use YYYY-MM-DD.")
                return 1
        totals_by_source: dict[str, int] = {}
        totals_by_project: dict[str, int] = {}
        total_amount = 0
        total_events = 0
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = _json.loads(line)
                except Exception:
                    continue
                if since_dt:
                    try:
                        ev_ts = datetime.fromisoformat(ev.get("ts", "").replace("Z", "+00:00"))
                        if ev_ts < since_dt:
                            continue
                    except Exception:
                        continue
                amt = int(ev.get("amount", 0) or 0)
                src = str(ev.get("source", "unknown"))
                proj = str(ev.get("project_root") or "unknown")
                totals_by_source[src] = totals_by_source.get(src, 0) + amt
                totals_by_project[proj] = totals_by_project.get(proj, 0) + amt
                total_amount += amt
                total_events += 1
        print(f"Burnless global metrics ({total_events} events, since={since or 'beginning'})")
        print(f"  Total burnless_tokens: {total_amount:,}")
        print(f"  Estimated cost avoided (rough $15/MTok): ${(total_amount/1_000_000)*15.0:.4f}")
        print()
        print("By source:")
        for src, amt in sorted(totals_by_source.items(), key=lambda x: -x[1]):
            print(f"  {src:32s} {amt:>12,}")
        print()
        print("By project:")
        for proj, amt in sorted(totals_by_project.items(), key=lambda x: -x[1]):
            proj_short = proj if len(proj) <= 50 else "..." + proj[-47:]
            print(f"  {proj_short:50s} {amt:>12,}")
        return 0

    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])

    snapshot_label = getattr(args, "snapshot", None)
    if snapshot_label:
        snap = metrics_mod.session_snapshot(p["metrics"], label=snapshot_label)
        print(f"snapshot saved: {snapshot_label} @ {snap['ts']}")
        print(f"  burnless_tokens={snap['burnless_tokens']:,}  encoder={snap['encoder_calls']}  decoder={snap['decoder_calls']}  brain={snap['brain_calls']}")
        return 0

    if getattr(args, "diff", False):
        diff = metrics_mod.session_diff(p["metrics"])
        print(dashboard.render_session_diff(diff))
        return 0

    m = metrics_mod.load(p["metrics"])
    show_cost = bool(cfg.get("metrics", {}).get("show_estimated_cost", True))
    print(dashboard.render_metrics(m, show_cost=show_cost))
    return 0


def cmd_metrics_desktop(args: argparse.Namespace) -> int:
    turns_path = Path.home() / ".burnless" / "desktop" / "turns.jsonl"
    if not turns_path.exists():
        print(f"desktop metrics: no turns file at {turns_path}")
        return 0

    rows: list[dict] = []
    with turns_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)

    if not rows:
        print(f"desktop metrics: no valid rows in {turns_path}")
        return 0

    total_input = sum(int(row.get("input_tokens", 0) or 0) for row in rows)
    total_output = sum(int(row.get("output_tokens", 0) or 0) for row in rows)
    total_cache_read = sum(int(row.get("cache_read_tokens", 0) or 0) for row in rows)

    latency_values = [
        int(row["latency_ms"])
        for row in rows
        if row.get("latency_ms") is not None
    ]
    avg_latency = (sum(latency_values) / len(latency_values)) if latency_values else 0.0

    cumulative_compression_ratio = 0.0
    for row in rows:
        ratio = row.get("compression_ratio")
        if ratio is not None:
            cumulative_compression_ratio += float(ratio or 0)
            continue
        original = int(row.get("user_tokens_original", 0) or 0)
        compressed = int(row.get("user_tokens_compressed", 0) or 0)
        if compressed > 0:
            cumulative_compression_ratio += original / compressed

    print(f"Desktop turns: {len(rows)}")
    print(f"Avg latency: {avg_latency:.1f} ms")
    print(f"Total tokens: {total_input + total_output}")
    print(f"Total cache_read: {total_cache_read}")
    print(f"Cumulative compression_ratio: {cumulative_compression_ratio:.4f}")
    print(f"Source: {turns_path}")
    return 0


def cmd_brain(args: argparse.Namespace) -> int:
    from rich import print as rprint  # noqa: F401
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    _console = Console()
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    _load_anthropic_key()
    state = state_mod.load(p["state"])
    cfg = config_mod.load(p["config"])
    model = args.model or state.get("brain_model") or "claude-opus-4-7"
    history_path = p["root"] / "maestro" / "brain_history.jsonl"
    _inflight_lock = threading.Lock()

    def slash_help() -> str:
        return brain_adapters.render_commands()

    def available_maestro_models() -> list[str]:
        return brain_adapters.available_maestro_models(cfg, model)

    def render_maestro() -> str:
        adapter = brain_adapters.current_anthropic_adapter(model)
        brain_models = brain_adapters.available_brain_models(model)
        lines = [
            f"Current Brain: {model}",
            f"Adapter: {adapter.label} ({adapter.status})",
            "",
            "Switch with /maestro <model>  (Anthropic SDK only):",
        ]
        for candidate in brain_models:
            marker = "*" if candidate == model else " "
            lines.append(f"  {marker} {candidate}")
        lines.extend(
            [
                "",
                "Codex / Ollama as Brain: planned for v0.6.",
                "Use /workers to see configured worker adapters.",
            ]
        )
        return "\n".join(lines)

    def set_maestro(next_model: str) -> str:
        nonlocal model
        next_model = next_model.strip()
        if not next_model:
            return render_maestro()
        if not next_model.startswith("claude-"):
            return (
                f"'{next_model}' não é um modelo válido para o Brain.\n"
                "O Brain usa o Anthropic SDK — passe um modelo claude-* "
                "(ex: claude-sonnet-4-6, claude-haiku-4-5-20251001).\n"
                "Codex e Ollama como Brain são planejados para v0.6. "
                "Use /workers para ver os worker adapters configurados."
            )
        model = next_model
        state = state_mod.load(p["state"])
        state["brain_model"] = model
        state_mod.save(p["state"], state)
        return f"Maestro set to: {model}"

    def run_one(message: str) -> int:
        from .codec import decoder as decoder_mod
        from .codec import encoder as encoder_mod
        from .codec.police import maybe_police
        from .maestro import brain as brain_mod
        from .maestro import dispatcher as dispatcher_mod
        from .maestro import session as maestro_session

        user_capsule, confidence = encoder_mod.encode(message, project_root=root.parent)
        user_capsule, was_corrected = maybe_police(
            message,
            user_capsule,
            confidence,
            project_root=root.parent,
        )
        if was_corrected:
            print("  [police] capsule corrected")
        next_capsule = user_capsule
        next_raw = message
        next_extra = {"confidence": confidence}
        delegate_depth = 0

        while True:
            history = maestro_session.load_history(history_path)
            history_messages = maestro_session.to_messages_array(history)
            maestro_session.append_turn(
                history_path,
                role="user",
                raw=next_raw,
                capsule=next_capsule,
                **next_extra,
            )

            think_chunks: list[str] = []

            def on_think_delta(chunk: str) -> None:
                think_chunks.append(chunk)

            _turn_state = state_mod.load(p["state"])
            state_mod.touch_activity(_turn_state)
            state_mod.save(p["state"], _turn_state)

            # H5: pre_brain_prompt — plugins may filter/transform prompt before Anthropic
            from . import plugin_loader as _pl
            _brain_plugins = _pl.load_plugins(Path.home() / ".burnless")
            _h5 = _pl.call_all_plugins(
                _brain_plugins, "pre_brain_prompt",
                {"hook": "pre_brain_prompt", "user_capsule": next_capsule, "history": history_messages, "system_blocks": []},
            )
            if _h5:
                next_capsule = _h5.get("user_capsule") or next_capsule
                if _h5.get("system_blocks") is not None:
                    history_messages = list(history_messages)

            try:
                _adapter = brain_adapters.load_adapter(cfg, model)
                with _inflight_lock:
                    result = brain_mod.run_brain_turn(
                        user_capsule=next_capsule,
                        history_messages=history_messages,
                        project_root=root.parent,
                        model=model,
                        on_think_delta=on_think_delta,
                        adapter=_adapter,
                    )
            except Exception as e:
                print(f"brain error: {e}", file=sys.stderr)
                return 1

            think_text = result.get("think_text") or "".join(think_chunks).strip()
            if think_text:
                _console.print(
                    Panel(
                        think_text,
                        title="[dim cyan]THINK[/dim cyan]",
                        border_style="dim cyan",
                        expand=False,
                    )
                )

            capsule_text = result.get("capsule_text") or ""
            _raw_body = result.get("raw_body") or ""
            _delegate_lines_raw = result.get("delegate_lines") or []

            # H6: post_brain_output — plugins may filter capsule_text before decoder
            _h6 = _pl.call_all_plugins(
                _brain_plugins, "post_brain_output",
                {"hook": "post_brain_output", "capsule_text": capsule_text, "raw_body": _raw_body, "delegate_lines": _delegate_lines_raw},
            )
            if _h6:
                capsule_text = _h6.get("capsule_text") if "capsule_text" in _h6 else capsule_text
                _raw_body = _h6.get("raw_body") if "raw_body" in _h6 else _raw_body
                if "delegate_lines" in _h6:
                    result = dict(result)
                    result["delegate_lines"] = _h6["delegate_lines"]

            comp_cfg = cfg.get("compression", {})
            friendly = comp_cfg.get("friendly", True)
            voice_match = comp_cfg.get("voice_match", True)  # V1 default ON
            if friendly:
                # Pass raw user message as voice_sample so decoder mirrors tone.
                # Set voice_match=false in config.yaml pra desligar (decoder fica robotic, ~5% cheaper).
                vs = message if voice_match else None
                decoded = decoder_mod.decode(capsule_text, project_root=root.parent, voice_sample=vs)
            else:
                decoded = capsule_text
            if decoded:
                print(decoded)

            usage = result.get("usage") or {}
            _console.print(
                "[dim]usage: "
                f"input={usage.get('input_tokens', 0)} "
                f"output={usage.get('output_tokens', 0)} "
                f"cache_write={usage.get('cache_creation_input_tokens', 0)} "
                f"cache_read={usage.get('cache_read_input_tokens', 0)}"
                "[/dim]"
            )

            delegate_lines = result.get("delegate_lines") or []
            maestro_session.append_turn(
                history_path,
                role="assistant",
                raw=result.get("raw_text") or "",
                capsule=capsule_text,
                think=think_text,
                delegates=delegate_lines,
                usage=usage,
            )
            _turn_state = state_mod.load(p["state"])
            state_mod.touch_activity(_turn_state)
            state_mod.save(p["state"], _turn_state)
            if not delegate_lines:
                return 0
            if delegate_depth >= 3:
                print("max delegate depth reached; stopping after 3 levels", file=sys.stderr)
                return 0

            _console.rule("[dim]delegating[/dim]", style="dim")
            for line in delegate_lines:
                _console.print(Text("  → ", style="yellow") + Text(line, style="yellow"))
            capsules = dispatcher_mod.run_all(
                delegate_lines,
                burnless_root=root,
                project_root=root.parent,
                config=cfg,
            )
            for capsule in capsules:
                _console.print(Text("  ✓ ", style="green") + Text(capsule))
            next_capsule = "\n".join(c for c in capsules if c.strip()) or "brz :: ERR worker returned empty capsule"
            next_raw = next_capsule
            delegate_depth += 1
            next_extra = {"source": "worker_results", "delegate_depth": delegate_depth}
            print()

    def handle_keepalive(arg: str) -> str:
        ka_cfg = cfg.setdefault("keepalive", {})
        if arg in {"on", "off"}:
            ka_cfg["enabled"] = arg == "on"
            try:
                config_mod.save(p["config"], cfg)
                persisted = " (saved)"
            except Exception:
                persisted = " (not saved)"
            if arg == "on":
                # Daily idle cost warning. 1 ping per ~50min, capped at
                # max_pings_per_session (default 24/day). Each ping = 1 input
                # + 1 output token. On Sonnet 4.6 ($3/M input + $15/M output)
                # that is ~$0.000018 per ping × 24 = ~$0.00043/day idle.
                # Negligible by design — but show it once so the user owns
                # the choice.
                return (
                    f"keepalive: enabled{persisted}\n"
                    f"  ATTENTION: forgetting keepalive on while idle costs "
                    f"~$0.00045 USD per full day idle on Sonnet (24 pings × ~$0.000018).\n"
                    f"  If that's worth keeping cache warm for you, leave it on. "
                    f"If not, /keepalive off."
                )
            return f"keepalive: disabled{persisted}"
        st = state_mod.load(p["state"])
        enabled = ka_cfg.get("enabled", False)
        last_ts = st.get("keepalive_last_ts") or "never"
        last_status = st.get("keepalive_last_status") or "-"
        next_ka = st.get("next_keepalive_ts") or "-"
        return (
            f"keepalive: {'enabled' if enabled else 'disabled'}\n"
            f"  last ping: {last_ts}  status={last_status}\n"
            f"  next scheduled: {next_ka}"
        )

    def handle_slash(message: str) -> int | None:
        if message in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if message == "/clear":
            os.system("clear")
            return None
        if message in {"/help", "/commands"}:
            print(slash_help())
            return None
        if message == "/workers":
            print(brain_adapters.render_workers(cfg))
            return None
        if message == "/native":
            print(brain_adapters.render_native(root.parent))
            return None
        if message == "/maestro" or message.startswith("/maestro "):
            print(set_maestro(message.removeprefix("/maestro").strip()))
            return None
        if message == "/model" or message.startswith("/model "):
            print(set_maestro(message.removeprefix("/model").strip()))
            return None
        if message in {"/keepalive", "/keepalive status"} or message.startswith("/keepalive "):
            arg = message.removeprefix("/keepalive").strip() or "status"
            print(handle_keepalive(arg))
            return None
        return 2

    from .keepalive import KeepaliveDaemon, keepalive_enabled_by_default
    from .maestro import brain as _brain_mod_ka

    _ka_adapter = brain_adapters.load_adapter(cfg, model)
    try:
        _system_prefix = _brain_mod_ka.build_system_blocks(
            project_root=root.parent, history_messages=[]
        )
    except Exception:
        _system_prefix = []
    _ka_daemon = KeepaliveDaemon(
        state_path=p["state"],
        cfg=cfg,
        adapter=_ka_adapter,
        system_prefix=_system_prefix,
        inflight_lock=_inflight_lock,
        model=model,
    )
    _ka_daemon.start()

    if args.message:
        message = args.message.strip()
        slash_result = handle_slash(message)
        if message.startswith("/") and slash_result in (0, None):
            _ka_daemon.stop()
            return 0
        result_code = run_one(args.message)
        _ka_daemon.stop()
        return result_code

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.completion import WordCompleter
        try:
            import prompt_toolkit.input.bracketed_paste  # noqa: F401
        except Exception:
            pass
    except ImportError:
        try:
            return _run_basic_brain_repl(run_one, handle_slash=handle_slash, model=model)
        finally:
            _ka_daemon.stop()

    print("Burnless Maestro chat — /help for commands, /exit to leave.")
    print(f"Maestro: {model}")
    print("Submit with Ctrl-D or Enter on an empty trailing line.")
    kb = KeyBindings()

    @kb.add("enter")
    def _(event) -> None:
        buf = event.app.current_buffer
        if buf.text.strip() and not buf.document.current_line.strip():
            buf.validate_and_handle()
        else:
            buf.insert_text("\n")

    @kb.add("c-d")
    def _(event) -> None:
        buf = event.app.current_buffer
        if buf.text.strip():
            buf.validate_and_handle()
        else:
            event.app.exit(exception=EOFError)

    session = PromptSession(
        multiline=True,
        prompt_continuation="  ",
        key_bindings=kb,
        completer=WordCompleter(
            list(brain_adapters.slash_commands(model)),
            ignore_case=True,
            match_middle=False,
        ),
        complete_while_typing=True,
    )
    try:
        while True:
            try:
                message = session.prompt("brain › ")
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            message = message.strip()
            if not message:
                continue
            slash_result = handle_slash(message)
            if slash_result == 0:
                return 0
            if slash_result is None:
                continue
            code = run_one(message)
            if code:
                return code
            print()
    finally:
        _ka_daemon.stop()


def _run_basic_brain_repl(run_one, *, handle_slash=None, model: str | None = None) -> int:
    print("Burnless Maestro chat — /help for commands, /exit to leave.")
    if model:
        print(f"Maestro: {model}")
    print("prompt_toolkit unavailable; using basic multiline input.")
    print("Submit with an empty trailing line or Ctrl-D.")
    while True:
        print("brain › ", end="", flush=True)
        lines: list[str] = []
        try:
            while True:
                line = input()
                if not line and lines:
                    break
                if not line and not lines:
                    continue
                lines.append(line)
        except EOFError:
            if not lines:
                print()
                return 0
        message = "\n".join(lines).strip()
        if not message:
            continue
        if handle_slash is not None:
            slash_result = handle_slash(message)
            if slash_result == 0:
                return 0
            if slash_result is None:
                continue
        else:
            if message in {"/exit", "/quit", "exit", "quit"}:
                return 0
            if message == "/clear":
                os.system("clear")
                continue
        code = run_one(message)
        if code:
            return code
        print()


def cmd_read(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    capsule_path = p["capsules"] / f"{args.id}.json"
    summary_path = p["temp"] / f"{args.id}.json"
    log_path = p["logs"] / f"{args.id}.log"

    # QTP-D: try capsule (finalized) → temp (mid-flight) → log (raw fallback)
    if capsule_path.exists():
        print(f"[capsule] {capsule_path}")
        print(capsule_path.read_text(encoding="utf-8"))
        return 0

    if summary_path.exists():
        raw = summary_path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
            worker_s = data.get("worker_status") or data.get("status", "?")
            _audit_obj = data.get("audit") if isinstance(data.get("audit"), dict) else {}
            if "audit_status" in data:
                audit_s = data["audit_status"]
            else:
                _raw_ast = str(_audit_obj.get("status") or "").upper()
                if not _raw_ast or _raw_ast in {"SKIPPED", "UNAVAILABLE"}:
                    audit_s = "SKIP"
                elif _raw_ast in {"OK", "PASS"}:
                    audit_s = "OK"
                else:
                    audit_s = _raw_ast or "SKIP"
            test_s = data.get("test_status") or _extract_test_status(data)
            print(f"worker: {worker_s} | audit: {audit_s} | tests: {test_s}")
        except (json.JSONDecodeError, AttributeError):
            pass
        print(raw)
        return 0

    if log_path.exists():
        print(f"[log fallback — no summary for {args.id}, raw worker output:]", file=sys.stderr)
        print(log_path.read_text(encoding="utf-8"))
        return 0

    print(f"burnless: no record of {args.id} (capsule, summary, or log)", file=sys.stderr)
    return 2


def cmd_log(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    log_path = p["logs"] / f"{args.id}.log"
    if not log_path.exists():
        print(f"burnless: no log for {args.id}", file=sys.stderr)
        return 2
    print(log_path.read_text(encoding="utf-8"))
    return 0


def cmd_capsule(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    capsule_path = p["capsules"] / f"{args.id}.json"
    summary_path = p["temp"] / f"{args.id}.json"
    log_path = p["logs"] / f"{args.id}.log"
    deleg_path = p["delegations"] / f"{args.id}.md"

    if args.mode is None:
        if not capsule_path.exists():
            print(f"burnless: no capsule for {args.id} (run it first?)", file=sys.stderr)
            return 2
        print(capsule_path.read_text(encoding="utf-8"))
        return 0

    args.mode = compression_mod.normalize_mode(args.mode)
    if args.mode not in compression_mod.MODES:
        print(
            f"burnless: invalid mode {args.mode!r}; pick one of {compression_mod.MODES}",
            file=sys.stderr,
        )
        return 2
    if not summary_path.exists() or not log_path.exists():
        print(
            f"burnless: cannot regenerate; need both {summary_path} and {log_path}",
            file=sys.stderr,
        )
        return 2

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    raw_log = log_path.read_text(encoding="utf-8")
    goal = ""
    if deleg_path.exists():
        goal = _parse_goal_from_delegation(deleg_path.read_text(encoding="utf-8")) or ""

    capsule = compression_mod.compress(
        delegation_id=args.id,
        goal=goal or summary.get("summary", ""),
        summary=summary,
        raw_log=raw_log,
        mode=args.mode,
    )
    savings = compression_mod.measure_savings(raw_log, capsule)
    capsule.tokens = savings
    compression_mod.write(capsule_path, capsule)
    print(
        f"capsule {args.id} regenerated in mode={args.mode}: "
        f"{savings['raw_tokens']}t → {savings['capsule_tokens']}t "
        f"(×{savings['compression_ratio']}, saved {savings['saved_tokens']}t)"
    )
    print(f"  {capsule_path}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from . import liveness as _live
    bl_root = _resolve_burnless_root()
    if bl_root is None:
        print("burnless: no .burnless/ directory found. Run `burnless init` first.",
              file=sys.stderr)
        return 2
    follow = not args.no_follow
    try:
        for ev in _live.tail_events(bl_root, args.did, since_n=args.since, follow=follow):
            print(json.dumps(ev, ensure_ascii=False), flush=True)
    except KeyboardInterrupt:
        return 0
    except FileNotFoundError:
        print(f"burnless: no liveness file for {args.did}", file=sys.stderr)
        return 1
    return 0


def cmd_compress(args: argparse.Namespace) -> int:
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        print(
            "burnless compress: no input. Pipe a transcript or pass --file <path>.\n"
            "  Example: cat session.log | burnless compress\n"
            "           burnless compress --file session.log",
            file=sys.stderr,
        )
        return 2

    _load_anthropic_key()
    root = paths_mod.find_root() or paths_mod.root(Path.cwd())
    cfg = config_mod.load(root / "config.yaml")
    mode = args.level or cfg.get("compression", {}).get("mode", compression_mod.DEFAULT_MODE)
    mode = compression_mod.normalize_mode(mode)
    try:
        capsule_text, stats = compression_mod.compress_transcript(
            text,
            mode=mode,
            session_context=[],
        )
    except ValueError as e:
        print(f"burnless: {e}", file=sys.stderr)
        return 2

    try:
        from .codec.cipher import unpack_metadata

        version, session_id, key_ref = unpack_metadata(capsule_text)
    except ValueError as e:
        print(f"burnless: {e}", file=sys.stderr)
        return 2

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = root / "sessions" / f"{session_id}.capsule"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(capsule_text, encoding="utf-8")
    print(
        f"capsule [{session_id}] {version}/{key_ref} — "
        f"{stats['original_chars']}c → {stats['capsule_chars']}c "
        f"({stats['ratio']}%) saved: {out_path}"
    )
    if version == "v2":
        print("note: v2 decode requires the local in-memory keyring for this process.")
    return 0


def cmd_decode(args: argparse.Namespace) -> int:
    if args.file:
        capsule_text = Path(args.file).read_text(encoding="utf-8").strip()
    elif args.capsule:
        capsule_text = args.capsule.strip()
    else:
        print("burnless: provide a capsule string or --file path", file=sys.stderr)
        return 2

    try:
        from .codec.cipher import decode as cipher_decode, unpack

        _session_id, key, ciphertext = unpack(capsule_text)
        print(cipher_decode(ciphertext, key))
    except Exception as e:
        print(f"burnless: decode failed: {e}", file=sys.stderr)
        return 2
    return 0


def _resolve_burnless_root() -> Path | None:
    cwd = Path.cwd()
    for candidate in [cwd, *cwd.parents]:
        bl = candidate / ".burnless"
        if bl.is_dir():
            return bl
    return None


def cmd_warm_init(args: argparse.Namespace) -> int:
    bl_root = _resolve_burnless_root()
    if bl_root is None:
        print("burnless: no .burnless/ directory found. Run `burnless init` first.", file=sys.stderr)
        return 2
    provider = getattr(args, "provider", "both")
    results = {}
    if provider in ("claude", "both"):
        from . import warm_session as ws_claude
        model = getattr(args, "model", None) or "claude-sonnet-4-6"
        print(f"burnless warm init [claude]: seeding warm session for {bl_root.parent} (model={model})...")
        try:
            state = ws_claude.init(bl_root, model=model)
            results["claude"] = state
            print(f"  claude warm initialized: uuid={state['uuid'][:8]}…")
            iu = state.get("init_usage") or {}
            print(f"  cache_read:  {iu.get('cache_read', 0):,}")
            print(f"  cache_write: {iu.get('cache_write', 0):,}")
        except Exception as e:
            print(f"  claude warm init FAILED: {e}", file=sys.stderr)
    if provider in ("codex", "both"):
        from . import warm_session_codex as ws_codex
        codex_model = getattr(args, "model", None) or "gpt-5.2"
        print(f"burnless warm init [codex]: seeding warm session for {bl_root.parent} (model={codex_model})...")
        try:
            state = ws_codex.init(bl_root, model=codex_model)
            results["codex"] = state
            cached = state.get("init_usage", {}).get("cached", 0)
            print(f"  codex warm initialized: uuid={state['uuid'][:8]}…  cached_input={cached}")
        except Exception as e:
            print(f"  codex warm init FAILED: {e}", file=sys.stderr)
    return 0 if results else 1


def cmd_warm_status(args: argparse.Namespace) -> int:
    bl_root = _resolve_burnless_root()
    if bl_root is None:
        print("burnless: no .burnless/ directory found.", file=sys.stderr)
        return 2
    provider = getattr(args, "provider", "both")
    if provider in ("claude", "both"):
        from . import warm_session as ws
        s = ws.status(bl_root)
        if not s.get("exists"):
            print("warm session [claude]: NOT INITIALIZED. Run `burnless warm init`.")
        else:
            print(f"warm session [claude] for {s.get('project_root')}:")
            print(f"  uuid:           {s.get('uuid')}")
            print(f"  alive:          {s.get('alive')}")
            print(f"  needs_refresh:  {s.get('needs_refresh')}")
            print(f"  age_minutes:    {s.get('age_minutes')}")
            print(f"  created_at:     {s.get('created_at')}")
            print(f"  last_used:      {s.get('last_used')}")
    if provider in ("codex", "both"):
        from . import warm_session_codex as ws_codex
        sc = ws_codex.status(bl_root)
        if not sc.get("exists"):
            print("warm session [codex]: NOT INITIALIZED. Run `burnless warm init --provider codex`.")
        else:
            print(f"warm session [codex] for {sc.get('project_root')}:")
            print(f"  uuid:             {sc.get('uuid')}")
            print(f"  alive:            {sc.get('alive')}")
            print(f"  needs_refresh:    {sc.get('needs_refresh')}")
            print(f"  age_s:            {sc.get('age_s')}")
            print(f"  last_cache_ratio: {sc.get('last_cache_ratio')}")
    return 0


def cmd_warm_refresh(args: argparse.Namespace) -> int:
    bl_root = _resolve_burnless_root()
    if bl_root is None:
        print("burnless: no .burnless/ directory found.", file=sys.stderr)
        return 2
    provider = getattr(args, "provider", "both")
    if provider in ("claude", "both"):
        from . import warm_session as ws
        # Refresh every warm session under claude/
        for path in ws.list_warm_files():
            model = path.stem
            if ws.needs_refresh(bl_root, model):
                try:
                    state = ws.refresh(bl_root, model=model)
                    ru = state.get("last_refresh_usage") or {}
                    print(f"warm [claude/{model}] refreshed at {state.get('last_used')}")
                    print(f"  cache_read:  {ru.get('cache_read', 0):,}")
                    print(f"  cache_write: {ru.get('cache_write', 0):,}")
                except Exception as e:
                    print(f"burnless warm refresh [claude/{model}]: failed — {e}", file=sys.stderr)
            else:
                print(f"warm [claude/{model}]: no refresh needed")
    if provider in ("codex", "both"):
        from . import warm_session_codex as ws_codex
        # Refresh every warm session under codex/
        for path in ws_codex.list_warm_files():
            model = path.stem
            if ws_codex.needs_refresh(bl_root, model):
                try:
                    state = ws_codex.refresh(bl_root, model=model)
                    ru = state.get("last_refresh_usage") or {}
                    print(f"warm [codex/{model}] refreshed at {state.get('last_used')}")
                    print(f"  cached: {ru.get('cached', 0):,}")
                except Exception as e:
                    print(f"burnless warm refresh [codex/{model}]: failed — {e}", file=sys.stderr)
            else:
                print(f"warm [codex/{model}]: no refresh needed")
    return 0


def cmd_warm_daemon(args: argparse.Namespace) -> int:
    import signal as _signal
    import subprocess
    from . import warm_daemon as wd

    bl_root = _resolve_burnless_root()
    if bl_root is None:
        print("burnless: no .burnless/ directory found. Run `burnless init` first.", file=sys.stderr)
        return 2

    action = args.daemon_action
    if action == "start":
        alive, pid = wd.is_running(bl_root)
        if alive:
            print(f"burnless: daemon already running (pid={pid})", file=sys.stderr)
            return 1
        proc = subprocess.Popen(
            [sys.executable, "-m", "burnless", "warm", "daemon", "run-fg"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(bl_root.parent),
        )
        time.sleep(0.5)
        alive2, pid2 = wd.is_running(bl_root)
        if alive2:
            print(f"burnless: warm daemon started (pid={pid2})")
            return 0
        print("burnless: daemon spawn likely failed (no PID file)", file=sys.stderr)
        return 1
    elif action == "stop":
        alive, pid = wd.is_running(bl_root)
        if not alive:
            print("burnless: no daemon running")
            return 0
        try:
            os.kill(pid, _signal.SIGTERM)
            print(f"burnless: SIGTERM sent to pid={pid}")
            return 0
        except OSError as e:
            print(f"burnless: kill failed: {e}", file=sys.stderr)
            return 1
    elif action == "status":
        alive, pid = wd.is_running(bl_root)
        log_path = wd.log_file_path(bl_root)
        print(f"daemon: {'RUNNING' if alive else 'stopped'} (pid={pid if alive else '-'})")
        print(f"log:    {log_path}")
        if log_path.exists():
            print("--- last 10 log lines ---")
            lines = log_path.read_text(encoding="utf-8").splitlines()[-10:]
            for ln in lines:
                print(f"  {ln}")
        return 0
    elif action == "run-fg":
        return wd.run_loop(bl_root)
    return 2


def cmd_trace(args: argparse.Namespace) -> int:
    from . import debugless as dbg

    bl_root = _resolve_burnless_root()
    if bl_root is None:
        print("burnless: no .burnless/ found", file=sys.stderr)
        return 2

    result = dbg.trace(bl_root, args.did, model=args.model, timeout=args.timeout)
    if not result["ok"]:
        print(f"debugless error: {result['error']}", file=sys.stderr)
        return 1

    did = result["did"]
    vestigials = result["vestigials"]
    loops = result["loops"]
    dead = result["dead_branches"]
    ghost = result["ghost_refs"]
    print(f"debugless: {did} — {len(vestigials)}V {len(loops)}L {len(dead)}D {len(ghost)}G")

    if not getattr(args, "no_capsule", False):
        cap_path = dbg.write_capsule(result, bl_root)
        print(f"capsule: {cap_path}")

    import json as _json
    print(_json.dumps(result, indent=2))
    return 0


def cmd_debugless_sweep(args: argparse.Namespace) -> int:
    from . import debugless as dbg

    bl_root = _resolve_burnless_root()
    if bl_root is None:
        print("burnless: no .burnless/ found", file=sys.stderr)
        return 2

    results = dbg.sweep(
        bl_root,
        since_hours=args.since_hours,
        limit=args.limit,
        model=args.model,
    )
    for r in results:
        did = r["did"]
        vestigials = r["vestigials"]
        loops = r["loops"]
        dead = r["dead_branches"]
        ghost = r["ghost_refs"]
        cap_path = dbg.write_capsule(r, bl_root)
        print(f"debugless: {did} — {len(vestigials)}V {len(loops)}L {len(dead)}D {len(ghost)}G → {cap_path}")

    print(f"total: {len(results)}")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    from . import setup_wizard
    return setup_wizard.run(
        non_interactive=bool(getattr(args, "non_interactive", False)),
        accept_all=bool(getattr(args, "yes", False)),
        project=getattr(args, "project", None),
    )


def cmd_maestro(args: argparse.Namespace) -> int:
    from . import maestro_runner
    out = maestro_runner.run_maestro(args.telegram, model=args.model or maestro_runner.DEFAULT_MODEL)
    if out.get("error"):
        print(f"burnless maestro: {out['error']}", file=sys.stderr)
        if out.get("raw"):
            print(out["raw"], file=sys.stderr)
        return 1
    print(out["telegram_out"])
    u = out.get("usage", {})
    cc = u.get("cache_creation_input_tokens", 0)
    cr = u.get("cache_read_input_tokens", 0)
    print(f"  · maestro cache_creation={cc} cache_read={cr} cost=${out.get('cost', 0.0):.4f}", file=sys.stderr)
    return 0


def cmd_do(args: argparse.Namespace) -> int:
    """Atomic delegate + run in a single command. Equivalent to `burnless do "prompt"`."""
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)

    # Build delegate args (same defaults as `burnless delegate`)
    delegate_args = argparse.Namespace(
        text=args.text,
        goal=None,
        success=None,
        tier=args.tier,
        chain=None,
        force=False,
        allow_relative_paths=getattr(args, "allow_relative_paths", False),
    )
    rc = cmd_delegate(delegate_args)
    if rc != 0:
        return rc

    # Read the id directly from the args object cmd_delegate just populated —
    # state.last_delegation is shared and gets overwritten by parallel cmd_do.
    did = getattr(delegate_args, "_allocated_did", None)
    if not did:
        print("burnless: delegate did not produce a delegation ID", file=sys.stderr)
        return 1

    # If --mode is requested, temporarily override config compression.mode
    _mode_override = getattr(args, "mode_override", None)
    _config_patched = False
    _orig_config_text: str | None = None
    if _mode_override:
        cfg = config_mod.load(p["config"])
        _orig_mode = cfg.get("compression", {}).get("mode", compression_mod.DEFAULT_MODE)
        if _orig_mode != _mode_override:
            _orig_config_text = p["config"].read_text(encoding="utf-8")
            cfg.setdefault("compression", {})["mode"] = _mode_override
            config_mod.save(p["config"], cfg)
            _config_patched = True

    run_args = argparse.Namespace(
        id=did,
        dry_run=False,
        timeout=600,
        stale_timeout_s=None,
        mode="plain",
        progress=None,
        maestro=False,
        no_maestro=False,
        no_cache_worker=False,
        cold_cache=getattr(args, "cold_cache", False),
    )
    try:
        rc = cmd_run(run_args)
    finally:
        if _config_patched and _orig_config_text is not None:
            p["config"].write_text(_orig_config_text, encoding="utf-8")

    return rc


def cmd_shell(args: argparse.Namespace) -> int:
    # `burnless shell` is an alias for `burnless pty` — the legacy REPL was
    # removed in v0.7.4 because pyd preserves Claude Code commands while
    # still wrapping the session in Burnless instrumentation.
    import sys
    print("\033[2mburnless shell → burnless pty (alias since v0.7.4)\033[0m", file=sys.stderr)
    return cmd_pty(args)


def cmd_pty(args: argparse.Namespace) -> int:
    from . import pty_shell

    return pty_shell.main(argv_extra=getattr(args, "args", None) or [])


def cmd_route(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    info = routing_mod.explain_route(args.text, cfg["routing"])
    agent = cfg["agents"][info["tier"]]
    print(f"tier:    {info['tier']}")
    print(f"agent:   {agent['name']}  ({agent['command']})")
    print(f"matched: {info['matched_keyword'] or '(default)'}")
    return 0




def _record_and_bump(
    p: dict[str, Path],
    *,
    source: str,
    amount: int,
    reason: str,
    delegation_id: str | None = None,
    extra: dict | None = None,
    usd_per_million: float = 15.0,
    capsules_delta: int = 0,
) -> dict:
    before = metrics_mod.load(p["metrics"])
    new_metrics = metrics_mod.record(
        p["metrics"],
        p["audit"],
        source=source,
        amount=amount,
        reason=reason,
        delegation_id=delegation_id,
        extra=extra,
        usd_per_million=usd_per_million,
    )
    before_usd = float(before.get("estimated_cost_avoided_usd", 0.0))
    after_usd = float(new_metrics.get("estimated_cost_avoided_usd", 0.0))
    lifetime_mod.bump(
        project_root=p["root"].parent,
        usd_delta=max(after_usd - before_usd, 0.0),
        capsules_delta=capsules_delta,
    )
    return new_metrics









def cmd_profile(args: argparse.Namespace) -> int:
    from . import profiles as profiles_mod
    sub = getattr(args, "profile_cmd", None)
    if sub == "list":
        names = profiles_mod.list_profiles()
        if names:
            print("\n".join(names))
        else:
            print("(no profiles found)")
        return 0
    if sub == "init":
        path = profiles_mod.init_profile(args.name, getattr(args, "template", None))
        print(f"created {path}")
        return 0
    if sub == "show":
        import yaml as _yaml
        cfg = profiles_mod.resolve_profile(args.name)
        print(_yaml.dump(cfg, sort_keys=False, allow_unicode=True), end="")
        return 0
    if sub == "switch":
        print(f"export BURNLESS_PROFILE={args.name}")
        return 0
    # no sub-subcommand
    import argparse as _ap
    args._parser.print_help()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="burnless", description=TAGLINE)
    p.add_argument("--version", action="version", version=f"burnless {__version__}")
    p.add_argument(
        "--profile", "-p",
        metavar="NAME",
        default=None,
        help="use a named profile from ~/.burnless/profiles/<NAME>.yaml (overrides BURNLESS_PROFILE env)",
    )
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("init", help="initialize .burnless/ in current directory")
    sp.add_argument("--project", help="project name (default: current dir name)")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--with-claude-md", action="store_true", dest="with_claude_md",
                    help="write a burnless block to CLAUDE.md in this directory (opt-in; default skips to avoid worker contamination)")
    sp.add_argument("--no-claude-md", action="store_true", dest="no_claude_md",
                    help="(deprecated, default now) explicitly skip CLAUDE.md creation")
    sp.add_argument("--claude-code", action="store_true", dest="claude_code",
                    help="install burnless agent/hook files into ~/.claude/ (opt-in)")
    sp.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="(with --claude-code) show what would be installed without writing files")
    sp.add_argument("--uninstall", action="store_true", dest="uninstall",
                    help="(with --claude-code) remove installed burnless files from ~/.claude/")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("rtk", help="toggle the RTK wrapper (token-saving CLI proxy) for tier commands")
    sp.add_argument("action", choices=["enable", "disable", "status"],
                    help="enable: prefix `rtk` to every agent command; disable: strip it; status: show current state")
    sp.set_defaults(func=cmd_rtk)

    sp = sub.add_parser("plan", help="set the project plan (compact state)")
    sp.add_argument("text")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("delegate", help="create a numbered delegation")
    sp.add_argument("text", help="task description")
    sp.add_argument("--goal", help="overall goal (defaults to task)")
    sp.add_argument("--success", help="success criteria")
    sp.add_argument("--tier", choices=["diamond", "gold", "silver", "bronze"], help="force tier (diamond = explicit escalation only)")
    sp.add_argument(
        "--chain",
        default=None,
        help="CSV of predecessor delegation IDs for lazy context (e.g. d042,d038)",
    )
    sp.add_argument(
        "--force",
        action="store_true",
        help="manually override the hard tier gate when selecting a higher tier",
    )
    sp.add_argument(
        "--chat",
        action="store_true",
        help="Maestro chat mode: render conversational template (no JSON schema, natural-text response)",
    )
    sp.add_argument(
        "--allow-relative-paths",
        action="store_true",
        dest="allow_relative_paths",
        help="skip the absolute-path guard (workers run in isolated cwd; relative paths may fail)",
    )
    sp.set_defaults(func=cmd_delegate)

    sp = sub.add_parser("run", help="execute a delegation through its agent")
    sp.add_argument("id")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--timeout", type=int, default=600)
    sp.add_argument("--stale-timeout-s", type=int, default=None, dest="stale_timeout_s")
    sp.add_argument(
        "--maestro",
        action="store_true",
        help="use the experimental Anthropic SDK Maestro backend instead of the configured Worker CLI",
    )
    sp.add_argument(
        "--no-maestro",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    sp.add_argument(
        "--no-cache-worker",
        action="store_true",
        help="force subprocess backend (claude -p) instead of CachedWorker API",
    )
    sp.add_argument(
        "--cold-cache",
        action="store_true",
        dest="cold_cache",
        help="inject a nonce into the system block to guarantee a cache miss (useful for cold-cache benchmarks)",
    )
    sp.add_argument(
        "--no-decode",
        action="store_true",
        help="skip Haiku roundtrip decode; print terse capsule status instead",
    )
    modes = sp.add_mutually_exclusive_group()
    modes.add_argument("--watch", action="store_const", const="watch", dest="mode", help="show a live worker panel")
    modes.add_argument("--quiet", action="store_const", const="quiet", dest="mode", help="show one-line running status")
    modes.add_argument("--full", action="store_const", const="full", dest="mode", help="stream raw output in real time")
    sp.add_argument(
        "--verbose",
        action="store_true",
        help="emit 3-line summary (status, body, reason) instead of single status line. Auto-on for TTY.",
    )
    sp.set_defaults(mode="plain")
    sp.add_argument(
        "--progress",
        choices=["minimal", "brief", "full"],
        default=None,
        help="progress detail level: minimal (spinner+phase), brief (ephemeral panel), full (raw stream). Overrides display.progress_detail config.",
    )
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("status", help="show project state + headline metric")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("metrics", help="show counters and estimated cost avoided")
    metrics_sub = sp.add_subparsers(dest="metrics_cmd")
    dsp = metrics_sub.add_parser("desktop", help="show desktop turn metrics from ~/.burnless/desktop/turns.jsonl")
    dsp.set_defaults(func=cmd_metrics_desktop)
    sp.add_argument(
        "--snapshot",
        metavar="LABEL",
        help="capture a metrics snapshot with this label (e.g. 'session_start', 'session_end')",
    )
    sp.add_argument(
        "--diff",
        action="store_true",
        help="show delta between the two most recent snapshots",
    )
    sp.add_argument("--global", dest="global_view", action="store_true",
                    help="Aggregate metrics across all projects from ~/.burnless/global_metrics.jsonl")
    sp.add_argument("--since", default=None,
                    help="ISO date (YYYY-MM-DD) to filter --global events")
    sp.set_defaults(func=cmd_metrics)

    sp = sub.add_parser("providers", help="inspect or reset multi-provider health stats")
    providers_sub = sp.add_subparsers(dest="providers_cmd")
    sp.set_defaults(func=lambda args, parser=sp: parser.print_help() or 0)
    psp = providers_sub.add_parser("stats", help="show provider health stats")
    psp.set_defaults(func=cmd_providers_stats)
    psp = providers_sub.add_parser("reset", help="clear provider health stats")
    psp.set_defaults(func=cmd_providers_reset)

    sp = sub.add_parser("provider", help="inspect or reset multi-provider health stats")
    provider_sub = sp.add_subparsers(dest="provider_cmd")
    sp.set_defaults(func=lambda args, parser=sp: parser.print_help() or 0)
    psp = provider_sub.add_parser("status", help="show provider health stats")
    psp.set_defaults(func=cmd_provider_status)
    psp = provider_sub.add_parser("reset", help="clear provider health stats")
    psp.set_defaults(func=cmd_provider_reset)

    sp = sub.add_parser("provider-stats", help="show provider health stats")
    sp.set_defaults(func=cmd_providers_stats)

    sp = sub.add_parser("provider-reset", help="clear provider health stats")
    sp.set_defaults(func=cmd_providers_reset)

    sp = sub.add_parser("decisions", help="inspect or clear the silver decisions cache")
    decisions_sub = sp.add_subparsers(dest="decisions_cmd")
    sp.set_defaults(func=lambda args, parser=sp: parser.print_help() or 0)
    dsp = decisions_sub.add_parser("list", help="list cached architectural decisions")
    dsp.add_argument("--json", action="store_true", help="emit raw JSON")
    dsp.set_defaults(func=cmd_decisions_list)
    dsp = decisions_sub.add_parser("clear", help="clear cached architectural decisions")
    dsp.set_defaults(func=cmd_decisions_clear)

    sp = sub.add_parser("brain", help="enter Maestro brain chat (model configurable in .burnless/config.yaml)")
    sp.add_argument("--message", "-m", help="single-shot mode")
    sp.add_argument("--model", default=None, help="override brain model")
    sp.set_defaults(func=cmd_brain)

    sp = sub.add_parser("read", help="print compact JSON summary for delegation ID")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("log", help="print raw log for delegation ID")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("capsule", help="show or regenerate the operational capsule for a delegation")
    sp.add_argument("id")
    sp.add_argument(
        "--mode",
        choices=list(compression_mod.MODES),
        default=None,
        help="regenerate capsule under this mode (light|balanced|extreme)",
    )
    sp.set_defaults(func=cmd_capsule)

    sp = sub.add_parser("watch", help="Stream liveness events from a delegation (.burnless/runs/<did>/liveness.jsonl)")
    sp.add_argument("did", help="delegation ID to watch (e.g. d378)")
    sp.add_argument("--since", type=int, default=0,
                    help="skip first N existing events before streaming")
    sp.add_argument("--no-follow", action="store_true",
                    help="print existing events and exit (do not tail)")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("compress", help="compress a transcript into a capsule")
    sp.add_argument("--file", "-f", default=None)
    sp.add_argument("--level", default=None, choices=["light", "balanced", "extreme"])
    sp.add_argument("--out", "-o", default=None)
    sp.set_defaults(func=cmd_compress)

    sp = sub.add_parser("decode", help="decode a burnless capsule")
    sp.add_argument("capsule", nargs="?", default=None)
    sp.add_argument("--file", "-f", default=None)
    sp.set_defaults(func=cmd_decode)

    sp = sub.add_parser("route", help="dry-run routing for a piece of text")
    sp.add_argument("text")
    sp.set_defaults(func=cmd_route)

    sp = sub.add_parser("setup", help="detect CLIs/keys and write a sensible config")
    sp.add_argument("--project", help="project name (default: current dir name)")
    sp.add_argument("--yes", "-y", action="store_true", help="accept all defaults")
    sp.add_argument("--non-interactive", action="store_true", help="no prompts")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("warm", help="manage the warm session pool (cache-hit prefix for workers)")
    sp.set_defaults(func=lambda args, parser=sp: parser.print_help() or 0)
    warm_sub = sp.add_subparsers(dest="warm_cmd")
    wsp = warm_sub.add_parser("init", help="create a warm session for this project and seed W0")
    wsp.add_argument("--model", default=None, help="model for warm session (default: claude-sonnet-4-6)")
    wsp.add_argument("--provider", choices=["claude", "codex", "both"], default="both",
                     help="Which warm pool to operate on (default: both)")
    wsp.set_defaults(func=cmd_warm_init)
    wsp = warm_sub.add_parser("status", help="show warm session age and aliveness")
    wsp.add_argument("--provider", choices=["claude", "codex", "both"], default="both",
                     help="Which warm pool to operate on (default: both)")
    wsp.set_defaults(func=cmd_warm_status)
    wsp = warm_sub.add_parser("refresh", help="send a fork heartbeat to refresh prompt-cache TTL")
    wsp.add_argument("--model", default=None, help="model for refresh call")
    wsp.add_argument("--provider", choices=["claude", "codex", "both"], default="both",
                     help="Which warm pool to operate on (default: both)")
    wsp.set_defaults(func=cmd_warm_refresh)

    wdp = warm_sub.add_parser("daemon", help="background daemon to keep warm pools hot")
    wdp.set_defaults(func=lambda args, parser=wdp: parser.print_help() or 0)
    daemon_sub = wdp.add_subparsers(dest="daemon_action", required=True)
    daemon_sub.add_parser("start",  help="spawn daemon in background (detached)")
    daemon_sub.add_parser("stop",   help="send SIGTERM to running daemon")
    daemon_sub.add_parser("status", help="show daemon PID + last log lines")
    daemon_sub.add_parser("run-fg", help="run daemon in foreground (debug)")
    wdp.set_defaults(func=cmd_warm_daemon)

    sp = sub.add_parser("shell", help="alias for `burnless pty` (legacy REPL removed in v0.7.4)")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="extra args passed to pty")
    sp.set_defaults(func=cmd_shell)

    sp = sub.add_parser("pty", help="spawn the real maestro CLI (claude/codex) with 🔥 Burnless header")
    sp.add_argument("args", nargs="*", help="extra args forwarded to the maestro binary")
    sp.set_defaults(func=cmd_pty)

    sp = sub.add_parser("profile", help="manage named profiles (~/.burnless/profiles/)")
    sp.set_defaults(func=cmd_profile, _parser=sp)
    profile_sub = sp.add_subparsers(dest="profile_cmd")
    psp = profile_sub.add_parser("list", help="list available profiles")
    psp.set_defaults(func=cmd_profile, _parser=psp, profile_cmd="list")
    psp = profile_sub.add_parser("init", help="create a new profile")
    psp.add_argument("name", help="profile name")
    psp.add_argument("--template", "-t", choices=["claude", "codex", "ollama", "antigrav"], default=None)
    psp.set_defaults(func=cmd_profile, _parser=psp, profile_cmd="init")
    psp = profile_sub.add_parser("show", help="print resolved YAML for a profile")
    psp.add_argument("name", help="profile name")
    psp.set_defaults(func=cmd_profile, _parser=psp, profile_cmd="show")
    psp = profile_sub.add_parser("switch", help="print export command to activate a profile")
    psp.add_argument("name", help="profile name")
    psp.set_defaults(func=cmd_profile, _parser=psp, profile_cmd="switch")

    sp = sub.add_parser(
        "do",
        help="delegate + run in one atomic step  (e.g. burnless do \"fix the tests\")",
    )
    sp.add_argument("text", help="task description / objective")
    sp.add_argument(
        "--tier",
        choices=["diamond", "gold", "silver", "bronze"],
        default=None,
        help="force a specific tier (diamond = explicit escalation only)",
    )
    sp.add_argument(
        "--mode",
        choices=["balanced", "extreme", "light"],
        default=None,
        dest="mode_override",
        help="compression mode for this run only (does not modify config permanently)",
    )
    sp.add_argument(
        "--cold-cache",
        action="store_true",
        dest="cold_cache",
        help="inject a nonce to force cache miss — use for cold-cache benchmarks",
    )
    sp.add_argument(
        "--allow-relative-paths",
        action="store_true",
        dest="allow_relative_paths",
        help="skip the absolute-path guard (workers run in isolated cwd; relative paths may fail)",
    )
    sp.set_defaults(func=cmd_do)

    sp = sub.add_parser(
        "maestro",
        help="invoke the Maestro conducting layer (isolated, stateless) with a compacted telegram",
    )
    sp.add_argument("telegram", help="compacted one-line telegram of intent (JSON)")
    sp.add_argument("--model", default=None, help="model for the Maestro layer (default haiku)")
    sp.set_defaults(func=cmd_maestro)

    sp = sub.add_parser(
        "pipeline",
        help="3-layer pipeline toggle (encoder/decoder layer via mcp__burnless__maestro)",
    )
    sp.add_argument("action", nargs="?", default="status",
                    choices=["on", "off", "status"],
                    help="action (default: status)")
    sp.add_argument("--compression-mode", default="tight",
                    choices=["tight", "balanced", "loose"],
                    dest="compression_mode",
                    help="compression mode when activating (default: tight)")
    sp.set_defaults(func=cmd_pipeline)

    sp = sub.add_parser(
        "cmd",
        help="Run shell command; capsule output via Haiku if > threshold (brain-side capsule layer)",
    )
    sp.add_argument("shell_cmd", help="Shell command to run (quote it)")
    sp.add_argument("--threshold", type=int, default=4000,
                    help="char count above which to capsule (default 4000)")
    sp.add_argument("--no-mask", action="store_true",
                    help="disable secret masking")
    sp.set_defaults(func=cmd_cmd)

    sp = sub.add_parser("trace", help="GoPro-trace a delegation via local ollama (Debugless)",
                        description="GoPro-trace a delegation via local ollama (Debugless)")
    sp.add_argument("did", help="delegation ID to trace (e.g. d378)")
    sp.add_argument("--model", default="qwen2.5-coder:7b", help="ollama model to use")
    sp.add_argument("--timeout", type=int, default=90, help="ollama timeout in seconds")
    sp.add_argument("--no-capsule", action="store_true", dest="no_capsule",
                    help="skip writing capsule, just print to stdout")
    sp.set_defaults(func=cmd_trace)

    sp = sub.add_parser("debugless", help="Debugless DEV tools")
    sp.set_defaults(func=lambda args, parser=sp: parser.print_help() or 0)
    dbg_sub = sp.add_subparsers(dest="debugless_cmd")
    dsp = dbg_sub.add_parser("sweep", help="trace all recent delegations (sequential)")
    dsp.add_argument("--since-hours", type=int, default=24, dest="since_hours",
                     help="look back N hours (default 24)")
    dsp.add_argument("--limit", type=int, default=10, help="max delegations to trace (default 10)")
    dsp.add_argument("--model", default="qwen2.5-coder:7b", help="ollama model to use")
    dsp.set_defaults(func=cmd_debugless_sweep)

    return p


def cmd_pipeline(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    project = root.parent if root.name == ".burnless" else root
    action = (getattr(args, "action", None) or "status").lower()
    if action == "on":
        mode = getattr(args, "compression_mode", None) or "tight"
        payload = pipeline_state_mod.activate(project, compression_mode=mode)
        print(f"✓ Burnless pipeline ON · mode={payload['compression_mode']} · project={project.name}")
        print(f"  state: {pipeline_state_mod._state_file(project)}")
        return 0
    if action == "off":
        if pipeline_state_mod.deactivate(project):
            print(f"✓ Burnless pipeline OFF · project={project.name}")
        else:
            print(f"(already off) project={project.name}")
        return 0
    if action == "status":
        state = pipeline_state_mod.read_state(project)
        if not state:
            print(f"OFF · project={project.name}")
        else:
            print(pipeline_state_mod.statusline(project))
        return 0
    print(f"unknown action '{action}'. Use: on | off | status", file=sys.stderr)
    return 2


def cmd_cmd(args: argparse.Namespace) -> int:
    return run_and_capsule(
        args.shell_cmd,
        threshold=args.threshold,
        secret_mask=not args.no_mask,
        project_root=Path.cwd(),
    )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        from . import shell
        return shell.main()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
