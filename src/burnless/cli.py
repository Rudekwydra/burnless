from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
import threading
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

_THOUGHT_HINTS = (
    "planeje", "plano", "plan", "design", "desenhe", "arquitetura", "architecture",
    "decida", "decidir", "decision", "analise", "análise", "analyze", "review",
    "investigue", "investigar", "inspect", "study", "estude", "spec", "brief",
    "proposta", "proposal", "brainstorm", "ideia", "idea",
)
_EXECUTION_HINTS = (
    "implemente", "implementar", "fix", "corrija", "corrigir", "patch", "test",
    "teste", "write", "escreva", "editar", "edit", "create", "criar", "run",
    "execute", "executar", "validate", "validar",
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




def cmd_init(args: argparse.Namespace) -> int:
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
    if not getattr(args, "no_claude_md", False):
        try:
            from . import __version__ as _v
        except ImportError:
            _v = "0.7.4"
        claude_md = cwd / "CLAUDE.md"
        action = claude_integration.write_or_update(
            claude_md, version=_v, project_name=initial_state["project"]
        )
        print(f"CLAUDE.md: {action} burnless block at {claude_md}")
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
    goal = args.goal or text
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
    state = state_mod.load(p["state"])
    state["last_delegation"] = did
    state_mod.save(p["state"], state)

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


def _build_retry_prompt(original_prompt: str, did: str, status: str, summary: dict) -> str:
    issues = summary.get("issues") or []
    evidence = summary.get("evidence") or []
    return (
        original_prompt.rstrip()
        + f"\n\n---\nRetry da delegação {did}. Sua tentativa anterior retornou {status}. "
        f"Issues: {', '.join(str(i) for i in issues) or 'nenhum especificado'}. "
        f"Evidence faltando: {', '.join(str(e) for e in evidence) or 'nenhum'}. "
        "Corrija e reenvie o JSON com os mesmos campos."
    )


def _build_audit_fix_prompt(original_prompt: str, did: str, audit: dict) -> str:
    issues = audit.get("issues") or []
    feedback = str(audit.get("feedback") or audit.get("summary") or "").strip()
    return (
        original_prompt.rstrip()
        + f"\n\n---\nAudit retornou PART. Issues: {', '.join(str(i) for i in issues) or 'nenhum'}. "
        + (f"{feedback} " if feedback else "")
        + "Corrija sem mudar o que estava OK."
    )


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
    # Bronze local codec (Free tier — Ollama qwen). Falls back to passthrough.
    from .codec import ollama_bronze
    codec_result = ollama_bronze.encode(prompt)
    if codec_result.used_ollama:
        prompt = codec_result.compressed_text
        metrics_mod.bump_ratio_observed(p["metrics"], codec_result.ratio)

    agent_cfg = cfg["agents"][tier]
    selected_agent_cfg, ranked_providers = _select_provider_cfg(agent_cfg, tier=tier)
    selected_provider = selected_agent_cfg.get("provider") or selected_agent_cfg.get("name")

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
            print(f"Running {did} with maestro/{tier} ({result['command'][1]})...")

    if result is None and use_cached_worker:
        from . import cached_worker as _cw
        model = MAESTRO_TIER_MODEL.get(tier, MAESTRO_TIER_MODEL["silver"])
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
                cwd=root.parent,
                tier_agents=cfg.get("agents", {}),
            )
            result = result_obj.to_dict()
        except Exception as e:
            print(f"Runner failed; falling back to plain runner. ({e})", file=sys.stderr)
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
                cwd=root.parent,
                append_log=True,
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
            else:
                print("EVIDENCE_MISSING", file=sys.stderr)
                _retry_msg = (
                    "\n\n---\nSua resposta não incluiu o campo evidence. "
                    "evidence é obrigatório para trabalho de execução. Inclua: comando exato executado, "
                    "path verificado, saída observada. Campo vazio = tarefa incompleta."
                )
                _retry_result = agents_mod.run(
                    selected_agent_cfg, prompt + _retry_msg, timeout=args.timeout, cwd=root.parent, tier=tier
                )
                with log_path.open("a", encoding="utf-8") as _lf:
                    _lf.write("\n\n--- EVIDENCE_RETRY ---\n" + _retry_result.get("stdout", "") + "\n")
                _retry_json = deleg_mod.extract_result_json(_retry_result.get("stdout", ""))
                if _retry_json is not None:
                    summary = normalize_worker_envelope(_retry_json)
                    summary["kind"] = _normalize_report_kind(summary.get("kind") or summary.get("report_kind") or _infer_kind_hint(prompt))
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
            _status = "ERR" if result["returncode"] != 0 else "PART"
            _summary = "(agent did not emit final JSON block)"
            _issue = "missing_final_json" if result["returncode"] == 0 else f"returncode={result['returncode']}"
        summary = normalize_worker_envelope({
            "id": did,
            "status": _status,
            "kind": _infer_kind_hint(prompt),
            "summary": _summary,
            "files_touched": [],
            "validated": [],
            "issues": [_issue],
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
    _audit_retry_enabled = bool(retry_cfg.get("audit_retry", True))
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

            if _is_stale:
                _retry_prompt_text = prompt
                _retry_timeout = min(stale_timeout_s * 2, int(args.timeout or stale_timeout_s * 2))
            else:
                _retry_prompt_text = _build_retry_prompt(prompt, did, _cur_status, summary)
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
                _r_issue = "stale_worker" if _r_stale else ("missing_final_json" if _r_rc == 0 else f"returncode={_r_rc}")
                _r_sum = normalize_worker_envelope({
                    "id": did,
                    "status": "ERR" if _r_rc != 0 else "PART",
                    "kind": _infer_kind_hint(prompt),
                    "summary": "(retry: agent did not emit final JSON block)",
                    "files_touched": [],
                    "validated": [],
                    "issues": [_r_issue],
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

    # ── Audit pass ───────────────────────────────────────────────────────────
    summary = _audit_summary_evidence(
        p,
        cfg=cfg,
        did=did,
        prompt=prompt,
        summary=summary,
        log_path=log_path,
        timeout=min(int(getattr(args, "timeout", 600) or 600), 180),
        cwd=root.parent,
    )

    # ── Audit PART retry (re-run worker with fix_prompt, re-audit) ────────────
    if _audit_retry_enabled:
        _audit_obj = summary.get("audit") if isinstance(summary.get("audit"), dict) else {}
        _audit_st = str(_audit_obj.get("status") or "").upper()
        _post_status = str(summary.get("status") or "").upper()
        _worker_did_real_work = _worker_status == "OK" or "rescued_from_stale" in (summary.get("issues") or [])
        if (
            _audit_st not in {"OK", "PASS", "SKIPPED", "UNAVAILABLE"}
            and _post_status in ("PART", "ERR")
            and not _worker_did_real_work
        ):
            _fix_prompt = _build_audit_fix_prompt(prompt, did, _audit_obj)
            print(f"[retry/audit] {did}: audit={_audit_st}, re-running worker", file=sys.stderr)
            try:
                _ar = agents_mod.run(
                    agent_cfg, _fix_prompt, timeout=int(args.timeout or 600), cwd=root.parent
                )
                with log_path.open("a", encoding="utf-8") as _lf:
                    _lf.write("\n\n--- AUDIT_RETRY ---\n" + _ar.get("stdout", "") + "\n")
                _ar_json = deleg_mod.extract_result_json(_ar.get("stdout", ""))
                if _ar_json is not None:
                    _ar_sum = normalize_worker_envelope(_ar_json)
                    _ar_sum["kind"] = _normalize_report_kind(
                        _ar_sum.get("kind") or _ar_sum.get("report_kind") or _infer_kind_hint(prompt)
                    )
                    _ar_sum["retry_count"] = summary.get("retry_count", 0) + 1
                    _ar_sum["retry_status"] = list(summary.get("retry_status") or []) + [_post_status]
                    summary = _audit_summary_evidence(
                        p,
                        cfg=cfg,
                        did=did,
                        prompt=prompt,
                        summary=_ar_sum,
                        log_path=log_path,
                        timeout=min(int(getattr(args, "timeout", 600) or 600), 180),
                        cwd=root.parent,
                    )
            except Exception as _ae:
                print(f"[retry/audit] worker retry failed: {_ae}", file=sys.stderr)

    summary["worker_status"] = _worker_status
    _audit_obj = summary.get("audit") if isinstance(summary.get("audit"), dict) else {}
    _raw_audit_st = str(_audit_obj.get("status") or "").upper()
    if not _raw_audit_st or _raw_audit_st in {"SKIPPED", "UNAVAILABLE"}:
        summary["audit_status"] = "SKIP"
    elif _raw_audit_st in {"OK", "PASS"}:
        summary["audit_status"] = "OK"
    else:
        summary["audit_status"] = _raw_audit_st
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
    state["last_status"] = f"{summary.get('status', '?')}:{did}"
    state["last_capsule"] = did
    state["last_capsule_mode"] = mode
    state["next"] = capsule.next or None
    state_mod.save(p["state"], state)

    # Short output — details via `burnless read/log/capsule/metrics`
    status_str = summary.get("status", "?")
    if interrupted and not stale:
        print("Worker stopped by user.")
    else:
        head = f"{status_str}:{did}"
        summary_text = (summary.get("summary") or "").strip()
        if summary_text:
            head = f"{head}\n{summary_text}"
        if status_str != "OK":
            audit = summary.get("audit") if isinstance(summary.get("audit"), dict) else {}
            feedback = str(audit.get("feedback") or summary.get("next") or "").strip()
            if feedback:
                head = f"{head}\nReason: {feedback[:180]}"
        print(head)
    return 0 if status_str == "OK" else 1


_HEX_RE = re.compile(r'\b([0-9a-f]{7,40})\b', re.IGNORECASE)
_ABS_PATH_RE = re.compile(r'(/[^\s,;:"\')\]]+)')
_VALIDATED_SIZE_RE = re.compile(r'([A-Za-z0-9_./\-]+\.[A-Za-z0-9]+).*?(\d+)\s*bytes', re.IGNORECASE)


def _audit_execution_filesystem(summary: dict, cwd: Path) -> dict | None:
    """QTP-A: filesystem-first audit for kind=execution reports.

    For execution-kind reports, hard evidence (files exist on disk + sizes
    match declared values) outweighs auditor prose nitpicks. Returns:
      - audit dict with status OK if all files_touched exist and validated
        sizes match within 1024B tolerance
      - audit dict with status FAIL if any declared file is missing or
        sizes mismatch
      - None if there's not enough evidence to decide (caller falls back
        to fast_path / LLM auditor ladder)

    QTP-B: when this returns OK, the runner does not downgrade worker OK
    based on prose-level audit issues — files on disk are the source of truth.
    """
    files_touched = summary.get("files_touched") or []
    if not isinstance(files_touched, list) or not files_touched:
        return None

    missing: list[str] = []
    for path_str in files_touched:
        if not isinstance(path_str, str) or not path_str:
            continue
        p = Path(path_str)
        if not p.is_absolute():
            p = cwd / p
        if not p.exists():
            missing.append(path_str)

    if missing:
        return {
            "status": "FAIL",
            "summary": f"Filesystem audit: {len(missing)} declared file(s) missing on disk",
            "evidence_checked": [str(x) for x in files_touched[:5]],
            "issues": [f"missing: {m}" for m in missing[:5]],
            "auditor_tier": "filesystem_first",
            "auditor_name": "filesystem_first",
            "attempted_tiers": [],
            "attempted_auditors": [],
        }

    validated = summary.get("validated") or []
    size_mismatches: list[str] = []
    if isinstance(validated, list):
        for entry in validated:
            m = _VALIDATED_SIZE_RE.search(str(entry))
            if not m:
                continue
            name, declared = m.group(1), int(m.group(2))
            actual_path: Path | None = None
            for ft in files_touched:
                if not isinstance(ft, str):
                    continue
                if name in ft:
                    p = Path(ft)
                    if not p.is_absolute():
                        p = cwd / p
                    if p.exists():
                        actual_path = p
                        break
            if actual_path is None:
                continue
            try:
                actual_size = actual_path.stat().st_size
            except OSError:
                continue
            if abs(actual_size - declared) > 1024:
                size_mismatches.append(
                    f"{name}: declared {declared}B, actual {actual_size}B"
                )

    if size_mismatches:
        return {
            "status": "FAIL",
            "summary": f"Filesystem audit: size mismatch in {len(size_mismatches)} file(s)",
            "evidence_checked": [str(x) for x in (validated[:3] if isinstance(validated, list) else [])],
            "issues": size_mismatches[:5],
            "auditor_tier": "filesystem_first",
            "auditor_name": "filesystem_first",
            "attempted_tiers": [],
            "attempted_auditors": [],
        }

    return {
        "status": "OK",
        "summary": f"Filesystem audit: {len(files_touched)} file(s) present on disk, sizes match",
        "evidence_checked": [str(x) for x in files_touched[:5]],
        "issues": [],
        "auditor_tier": "filesystem_first",
        "auditor_name": "filesystem_first",
        "attempted_tiers": [],
        "attempted_auditors": [],
    }


def _fast_path_check(evidence: list[str], cwd: Path) -> tuple[bool, str]:
    """Return (passed, reason) if evidence is deterministically verifiable without LLM.

    T1: 7+ consecutive hex chars verified with git cat-file -e.
    T2: absolute path that exists with size > 0.
    """
    combined = " ".join(evidence)
    for m in _HEX_RE.finditer(combined):
        h = m.group(1)
        try:
            r = subprocess.run(
                ["git", "cat-file", "-e", h],
                cwd=cwd,
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return True, f"git cat-file -e {h} → exit 0"
        except Exception:
            pass
    for m in _ABS_PATH_RE.finditer(combined):
        path_str = m.group(1).rstrip(".,;:\"'")
        try:
            fp = Path(path_str)
            if fp.exists() and fp.stat().st_size > 0:
                return True, f"stat({path_str}) → exists, size={fp.stat().st_size}"
        except Exception:
            pass
    return False, ""


def _summary_evidence(summary: dict) -> list[str]:
    raw = summary.get("evidence")
    if raw is None:
        raw = summary.get("validated")
    if not isinstance(raw, list):
        return []
    evidence: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            evidence.append(text[:180])
    return evidence


def _append_issue(summary: dict, issue: str) -> None:
    issues = summary.get("issues")
    if not isinstance(issues, list):
        issues = []
    if issue not in issues:
        issues.append(issue)
    summary["issues"] = issues


def _add_evidence_feedback(summary: dict, audit: dict | None = None) -> None:
    feedback = "Add concrete evidence: command, file, or check observed."
    current_next = str(summary.get("next") or "").strip()
    if feedback not in current_next:
        summary["next"] = f"{current_next} {feedback}".strip()
    if audit is not None:
        audit.setdefault("feedback", feedback)


def _audit_summary_evidence(
    p: dict[str, Path],
    *,
    cfg: dict,
    did: str,
    prompt: str,
    summary: dict,
    log_path: Path,
    timeout: int,
    cwd: Path,
) -> dict:
    summary = dict(summary)
    status = str(summary.get("status") or "").upper()
    summary["status"] = status or summary.get("status")
    kind = _normalize_report_kind(summary.get("kind") or summary.get("report_kind") or _infer_kind_hint(prompt))
    summary["kind"] = kind
    evidence = _summary_evidence(summary)
    if kind == "thought" and not evidence:
        audit = {
            "status": "SKIPPED",
            "summary": "Thought-only report; execution evidence not required.",
            "evidence_checked": [],
            "issues": [],
            "auditor_tier": None,
            "auditor_name": None,
            "attempted_tiers": [],
            "attempted_auditors": [],
        }
        summary["audit"] = audit
        return summary
    if status == "OK" and not evidence:
        summary["status"] = "PART"
        _append_issue(summary, "missing_evidence")
        _add_evidence_feedback(summary)
        status = "PART"
    if status not in {"OK", "PART"} or not evidence:
        return summary

    audit_path = p["temp"] / f"{did}.audit.json"

    # H8: pre_audit_call — plugin may override audit or replace the ladder
    from . import plugin_loader as _pl
    _plugins = _pl.load_plugins(Path.home() / ".burnless")
    auditors_ladder = cfg.get("audit", {}).get("auditors") or ["bronze", "silver", "gold"]
    _h8 = _pl.call_all_plugins(
        _plugins, "pre_audit_call",
        {"hook": "pre_audit_call", "did": did, "evidence": evidence, "summary": summary, "auditors_ladder": auditors_ladder},
    )
    if _h8:
        if _h8.get("audit") is not None:
            audit = dict(_h8["audit"])
            audit.setdefault("auditor_tier", "plugin")
            audit.setdefault("auditor_name", "plugin")
            audit.setdefault("attempted_tiers", [])
            audit.setdefault("attempted_auditors", [])
            _write_audit_result(audit_path, audit)
            summary["audit"] = audit
            # H4: audit_result_received
            _pl.call_all_plugins(
                _plugins, "audit_result_received",
                {"hook": "audit_result_received", "did": did, "audit": audit, "summary": summary},
            )
            return summary
        if _h8.get("override_ladder"):
            auditors_ladder = list(_h8["override_ladder"])

    # QTP-A/B: filesystem-first auditor for kind=execution. When the worker
    # declared files_touched, treat the filesystem as ground truth. Files
    # present + sizes match → audit OK regardless of any LLM prose nitpicks.
    # Files missing or size mismatch → audit FAIL with concrete reason.
    if kind == "execution":
        fs_audit = _audit_execution_filesystem(summary, cwd)
        if fs_audit is not None:
            # QTP-E: attach visual thumbnails for png/pdf/pptx/html artifacts
            from . import visual_review as _vr
            _vr_cfg = cfg.get("visual_review") or {}
            if _vr_cfg.get("enabled", True):
                _vr.attach_thumbnails(
                    summary, cwd,
                    enabled=True,
                    thumbnails=_vr_cfg.get("thumbnails", True),
                    max_size=int(_vr_cfg.get("max_size", 256)),
                    max_artifacts=int(_vr_cfg.get("max_artifacts", 5)),
                )
            _write_audit_result(audit_path, fs_audit)
            summary["audit"] = fs_audit
            if fs_audit["status"] == "FAIL" and status == "OK":
                summary["status"] = "PART"
                first_issue = (fs_audit.get("issues") or ["filesystem_audit_fail"])[0]
                _append_issue(summary, first_issue)
            _pl.call_all_plugins(
                _plugins, "audit_result_received",
                {"hook": "audit_result_received", "did": did, "audit": fs_audit, "summary": summary},
            )
            return summary

    # Fast-path: deterministic check before calling any LLM auditor (T1/T2).
    fp_passed, fp_reason = _fast_path_check(evidence, cwd)
    if fp_passed:
        audit = {
            "status": "OK",
            "summary": f"Fast-path: {fp_reason}",
            "evidence_checked": evidence[:3],
            "issues": [],
            "auditor_tier": "fast_path",
            "auditor_name": "fast_path",
            "attempted_tiers": [],
            "attempted_auditors": [],
        }
        _write_audit_result(audit_path, audit)
        summary["audit"] = audit
        # H4: audit_result_received
        _pl.call_all_plugins(_plugins, "audit_result_received", {"hook": "audit_result_received", "did": did, "audit": audit, "summary": summary})
        return summary

    log_excerpt = log_path.read_text(encoding="utf-8")[-12000:] if log_path.exists() else ""
    audit_prompt = _render_audit_prompt(did=did, prompt=prompt, summary=summary, log_excerpt=log_excerpt)
    agent_cfg = cfg.get("agents") or {}
    # auditors_ladder already set above (possibly overridden by H8)
    attempted_auditors = []
    unavailable = []
    audit = None
    for auditor_name in auditors_ladder:
        tier_cfg = agent_cfg.get(auditor_name)
        if not tier_cfg:
            continue
        attempted_auditors.append(auditor_name)
        if not agents_mod.is_available(tier_cfg):
            unavailable.append(f"{auditor_name} auditor unavailable")
            continue
        try:
            result = agents_mod.run(tier_cfg, audit_prompt, timeout=timeout, cwd=cwd)
        except Exception as exc:
            unavailable.append(f"{auditor_name} auditor failed to run: {exc}")
            continue
        audit = deleg_mod.extract_result_json(result.get("stdout", "")) or {
            "status": "FAIL",
            "summary": "Auditor did not emit final JSON.",
            "evidence_checked": evidence[:3],
            "issues": ["missing_audit_json"],
        }
        audit["auditor_tier"] = auditor_name  # legacy
        audit["auditor_name"] = auditor_name
        audit["attempted_tiers"] = attempted_auditors  # legacy
        audit["attempted_auditors"] = attempted_auditors
        break

    if audit is None:
        audit = {
            "status": "UNAVAILABLE",
            "summary": "; ".join(unavailable) or "No configured auditor tiers available.",
            "evidence_checked": evidence[:3],
            "issues": ["audit_unavailable"],
            "auditor_tier": None,
            "auditor_name": None,
            "attempted_tiers": attempted_auditors,
            "attempted_auditors": attempted_auditors,
        }
        _write_audit_result(audit_path, audit)
        summary["audit"] = audit
        if summary.get("status") == "OK":
            summary["status"] = "PART"
        _append_issue(summary, "audit_unavailable")
        _add_evidence_feedback(summary, audit)
        # H4: audit_result_received
        _pl.call_all_plugins(_plugins, "audit_result_received", {"hook": "audit_result_received", "did": did, "audit": audit, "summary": summary})
        return summary

    audit_status = str(audit.get("status") or "").upper()
    audit["status"] = audit_status or "FAIL"
    audit.setdefault("attempted_tiers", attempted_auditors)
    audit.setdefault("attempted_auditors", attempted_auditors)
    _write_audit_result(audit_path, audit)
    summary["audit"] = audit
    # H4: audit_result_received
    _pl.call_all_plugins(_plugins, "audit_result_received", {"hook": "audit_result_received", "did": did, "audit": audit, "summary": summary})
    if audit["status"] not in {"OK", "PASS"}:
        if summary.get("status") == "OK":
            summary["status"] = "PART"
        _append_issue(summary, "audit_failed")
        _add_evidence_feedback(summary, audit)
    return summary


def _write_audit_result(path: Path, audit: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, ensure_ascii=False)


def _render_audit_prompt(*, did: str, prompt: str, summary: dict, log_excerpt: str) -> str:
    return f"""\
You are the Burnless Auditor. Read-only: do not edit files or run commands.
Check whether the worker summary and evidence are supported by the delegation prompt and log excerpt.
Evidence must cite observable commands, files, logs, or checks, not opinions.

Delegation ID: {did}

Worker summary JSON:
```json
{json.dumps(summary, indent=2, ensure_ascii=False)}
```

Delegation prompt excerpt:
```
{prompt[:8000]}
```

Log tail:
```
{log_excerpt}
```

Return only a final JSON block:
```json
{{
  "status": "OK | FAIL",
  "summary": "<one short sentence>",
  "evidence_checked": [],
  "issues": []
}}
```
"""


def _infer_kind_hint(text: str) -> str:
    low = text.lower()
    thought_score = sum(1 for hint in _THOUGHT_HINTS if hint in low)
    exec_score = sum(1 for hint in _EXECUTION_HINTS if hint in low)
    if thought_score > exec_score:
        return "thought"
    if exec_score > thought_score:
        return "execution"
    return "execution"


def _normalize_report_kind(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"thought", "thinking", "design", "plan", "analysis"}:
        return "thought"
    if text in {"execution", "feito", "done", "implemented"}:
        return "execution"
    return "execution"


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


def cmd_setup(args: argparse.Namespace) -> int:
    from . import setup_wizard
    return setup_wizard.run(
        non_interactive=bool(getattr(args, "non_interactive", False)),
        accept_all=bool(getattr(args, "yes", False)),
        project=getattr(args, "project", None),
    )


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
    )
    rc = cmd_delegate(delegate_args)
    if rc != 0:
        return rc

    # Retrieve the ID that cmd_delegate just saved into state
    state = state_mod.load(p["state"])
    did = state.get("last_delegation")
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


def _count_capsules(path: Path) -> int:
    return len(list(path.glob("*.json")))


def _count_memory_entries(p: dict[str, Path]) -> int:
    index = p["root"] / "memories" / "index.json"
    if not index.exists():
        return 0
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(data.get("files", []) or [])


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


def _days_since(ts: str | None) -> int:
    if not ts:
        return 0
    started = datetime.fromisoformat(ts)
    now = datetime.now(started.tzinfo or timezone.utc)
    return max((now - started).days, 0)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_chain_from_delegation(md: str) -> list[str]:
    """Parse chain list from YAML front-matter at top of delegation markdown."""
    if not md.startswith("---"):
        return []
    end = md.find("---", 3)
    if end == -1:
        return []
    frontmatter = md[3:end].strip()
    for line in frontmatter.splitlines():
        if line.startswith("chain:"):
            value = line.split(":", 1)[1].strip().strip("[]")
            return [x.strip() for x in value.split(",") if x.strip()]
    return []


def _parse_tier_from_delegation(md: str) -> str | None:
    for line in md.splitlines():
        if line.lower().startswith("- **agent:**"):
            # "- **agent:** opus (gold)"
            if "(" in line and ")" in line:
                return line.rsplit("(", 1)[1].split(")", 1)[0].strip()
    return None


def _extract_test_status(summary: dict) -> str:
    items = list(summary.get("validated") or []) + list(summary.get("evidence") or [])
    for item in items:
        text = str(item).lower()
        if "pytest" in text or "passed" in text or "failed" in text:
            m = re.search(r"(\d+)\s+passed", text)
            if m:
                return f"OK:{m.group(1)}"
            m = re.search(r"(\d+)\s+failed", text)
            if m:
                return f"FAIL:{m.group(1)}"
            if "passed" in text:
                return "OK"
            if "failed" in text:
                return "FAIL"
    return "SKIP"


def _parse_created_at_from_delegation(md: str) -> str | None:
    """Extract created_at ISO timestamp from delegation markdown frontmatter."""
    import re as _re
    m = _re.search(r"\*\*created_at:\*\*\s*(\S+)", md)
    return m.group(1) if m else None


def _parse_goal_from_delegation(md: str) -> str | None:
    if "## Goal" not in md:
        return None
    after = md.split("## Goal", 1)[1]
    end = after.find("##")
    block = after[:end] if end != -1 else after
    text = " ".join(block.split())
    return text or None


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
    sp.add_argument("--no-claude-md", action="store_true", dest="no_claude_md",
                    help="skip writing the burnless block to CLAUDE.md")
    sp.set_defaults(func=cmd_init)

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
    sp.set_defaults(func=cmd_do)

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

    return p


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
