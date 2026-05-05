from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
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
from . import brain_adapters
from . import dashboard
from . import live_runner
from .estimator import estimate_tokens

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


def _with_runtime_context(prompt: str, *, project_root: Path, burnless_root: Path) -> str:
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
    context = (
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
    return f"{prompt.rstrip()}\n\n{context}"




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
    config_mod.write_default(p["config"])
    cfg = config_mod.load(p["config"])
    initial_state = dict(state_mod.DEFAULT_STATE)
    initial_state["project"] = args.project or cwd.name
    state_mod.save(p["state"], initial_state)
    metrics_mod.save(p["metrics"], metrics_mod._fresh())
    lifetime_mod.bump(project_root=cwd)
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
    agent_cfg = cfg["agents"][tier]

    did = state_mod.alloc_delegation_id(p["state"])
    goal = args.goal or text
    success = args.success or "task completed; final JSON block emitted as required."
    kind_hint = _infer_kind_hint(text)
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
    deleg_mod.write_delegation(deleg_path, body)
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


def cmd_run(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    state = state_mod.load(p["state"])
    metrics = metrics_mod.load(p["metrics"])
    did = args.id
    deleg_path = p["delegations"] / f"{did}.md"
    if not deleg_path.exists():
        print(f"burnless: delegation {did} not found at {deleg_path}", file=sys.stderr)
        return 2
    prompt = _with_runtime_context(
        deleg_path.read_text(encoding="utf-8"),
        project_root=root.parent,
        burnless_root=root,
    )

    # which tier did we pick at delegate time?
    # cheap parse: look at "agent:" line in the markdown
    tier = _parse_tier_from_delegation(prompt) or "bronze"
    agent_cfg = cfg["agents"][tier]

    if args.dry_run:
        print(f"[dry-run] would run: {' '.join(agents_mod.resolve_command(agent_cfg))}")
        print(f"[dry-run] prompt size: {len(prompt)} chars (~{estimate_tokens(prompt)} tokens)")
        return 0

    if not agents_mod.is_available(agent_cfg):
        print(
            f"burnless: agent binary not in PATH for tier {tier} ({agent_cfg.get('name')}).",
            file=sys.stderr,
        )
        print(f"  configured command: {agent_cfg['command']}", file=sys.stderr)
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
    stale_timeout_s = int(cfg.get("display", {}).get("stale_timeout_seconds", 300))

    # Persist a lightweight run snapshot before handing off to the worker.
    runs_dir = p["runs"]
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_plan = {
        "id": did,
        "tier": tier,
        "agent": agent_cfg.get("name"),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "delegation": str(p["delegations"] / f"{did}.md"),
    }
    (runs_dir / f"{did}.plan.json").write_text(
        json.dumps(run_plan, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    use_maestro = _should_use_maestro_backend(args, cfg, tier)
    result: dict | None = None
    backend_used = "subprocess"
    if use_maestro:
        result = _run_with_maestro(
            p, did=did, tier=tier, agent_cfg=agent_cfg, prompt=prompt, log_path=log_path,
        )
        if result is not None:
            backend_used = "maestro"
            print(f"Running {did} with maestro/{tier} ({result['command'][1]})...")

    if result is None:
        try:
            result_obj = live_runner.run_with_live_panel(
                delegation_id=did,
                tier=tier,
                agent_cfg=agent_cfg,
                prompt=prompt,
                log_path=log_path,
                mode=run_mode,
                burnless_tokens=bt_before,
                timeout=args.timeout,
                stale_timeout=stale_timeout_s,
                cwd=root.parent,
            )
            result = result_obj.to_dict()
        except Exception as e:
            print(f"Runner failed; falling back to plain runner. ({e})", file=sys.stderr)
            print(f"Running {did} with {tier}/{agent_cfg['name']}...")
            result = agents_mod.run(agent_cfg, prompt, timeout=args.timeout, cwd=root.parent)
            deleg_mod.write_log(log_path, result)

    # Always isolate raw log out of the main context.
    raw_size = estimate_tokens(result.get("stdout", "")) + estimate_tokens(result.get("stderr", ""))
    _record_and_bump(
        p,
        source="raw_logs_isolated",
        amount=raw_size,
        reason=f"raw stdout/stderr from {agent_cfg['name']} kept out of main context",
        delegation_id=did,
        usd_per_million=cfg["metrics"]["expensive_model_usd_per_million"],
    )

    interrupted = bool(result.get("interrupted"))
    stale = bool(result.get("stale"))
    extracted_json = deleg_mod.extract_result_json(result.get("stdout", ""))
    if extracted_json is not None:
        summary = extracted_json
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
                    agent_cfg, prompt + _retry_msg, timeout=args.timeout, cwd=root.parent
                )
                with log_path.open("a", encoding="utf-8") as _lf:
                    _lf.write("\n\n--- EVIDENCE_RETRY ---\n" + _retry_result.get("stdout", "") + "\n")
                _retry_json = deleg_mod.extract_result_json(_retry_result.get("stdout", ""))
                if _retry_json is not None:
                    summary = _retry_json
                    summary["kind"] = _normalize_report_kind(summary.get("kind") or summary.get("report_kind") or _infer_kind_hint(prompt))
    elif backend_used == "maestro" and result["returncode"] == 0 and not interrupted:
        # Maestro mode: assistant text without explicit JSON still counts as OK.
        snippet = (result.get("stdout") or "").strip().splitlines()
        first_line = snippet[0] if snippet else ""
        summary = {
            "id": did,
            "status": "OK",
            "kind": _infer_kind_hint(prompt),
            "summary": first_line[:160] or "Maestro turn completed.",
            "files_touched": [],
            "validated": [],
            "issues": [],
            "next": "",
        }
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
        summary = {
            "id": did,
            "status": _status,
            "kind": _infer_kind_hint(prompt),
            "summary": _summary,
            "files_touched": [],
            "validated": [],
            "issues": [_issue],
            "next": "",
        }
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
        return summary

    log_excerpt = log_path.read_text(encoding="utf-8")[-12000:] if log_path.exists() else ""
    audit_prompt = _render_audit_prompt(did=did, prompt=prompt, summary=summary, log_excerpt=log_excerpt)
    agent_cfg = cfg.get("agents") or {}
    auditors_ladder = cfg.get("audit", {}).get("auditors") or ["bronze", "silver", "gold"]
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
        return summary

    audit_status = str(audit.get("status") or "").upper()
    audit["status"] = audit_status or "FAIL"
    audit.setdefault("attempted_tiers", attempted_auditors)
    audit.setdefault("attempted_auditors", attempted_auditors)
    _write_audit_result(audit_path, audit)
    summary["audit"] = audit
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


def cmd_metrics(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    m = metrics_mod.load(p["metrics"])
    show_cost = bool(cfg.get("metrics", {}).get("show_estimated_cost", True))
    print(dashboard.render_metrics(m, show_cost=show_cost))
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

            try:
                result = brain_mod.run_brain_turn(
                    user_capsule=next_capsule,
                    history_messages=history_messages,
                    project_root=root.parent,
                    model=model,
                    on_think_delta=on_think_delta,
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
        return 2

    if args.message:
        message = args.message.strip()
        slash_result = handle_slash(message)
        if message.startswith("/") and slash_result in (0, None):
            return 0
        return run_one(args.message)

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.completion import WordCompleter
        try:
            import prompt_toolkit.input.bracketed_paste  # noqa: F401
        except Exception:
            pass
    except ImportError:
        return _run_basic_brain_repl(run_one, handle_slash=handle_slash, model=model)

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
    summary_path = p["temp"] / f"{args.id}.json"
    if not summary_path.exists():
        print(f"burnless: no summary for {args.id} (run it first?)", file=sys.stderr)
        return 2
    print(summary_path.read_text(encoding="utf-8"))
    return 0


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


def cmd_shell(args: argparse.Namespace) -> int:
    from . import shell

    return shell.main()


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


def _parse_tier_from_delegation(md: str) -> str | None:
    for line in md.splitlines():
        if line.lower().startswith("- **agent:**"):
            # "- **agent:** opus (gold)"
            if "(" in line and ")" in line:
                return line.rsplit("(", 1)[1].split(")", 1)[0].strip()
    return None


def _parse_goal_from_delegation(md: str) -> str | None:
    if "## Goal" not in md:
        return None
    after = md.split("## Goal", 1)[1]
    end = after.find("##")
    block = after[:end] if end != -1 else after
    text = " ".join(block.split())
    return text or None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="burnless", description=TAGLINE)
    p.add_argument("--version", action="version", version=f"burnless {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("init", help="initialize .burnless/ in current directory")
    sp.add_argument("--project", help="project name (default: current dir name)")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("plan", help="set the project plan (compact state)")
    sp.add_argument("text")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("delegate", help="create a numbered delegation")
    sp.add_argument("text", help="task description")
    sp.add_argument("--goal", help="overall goal (defaults to task)")
    sp.add_argument("--success", help="success criteria")
    sp.add_argument("--tier", choices=["gold", "silver", "bronze"], help="force tier")
    sp.add_argument(
        "--force",
        action="store_true",
        help="manually override the hard tier gate when selecting a higher tier",
    )
    sp.set_defaults(func=cmd_delegate)

    sp = sub.add_parser("run", help="execute a delegation through its agent")
    sp.add_argument("id")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--timeout", type=int, default=600)
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
    sp.set_defaults(func=cmd_metrics)

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

    sp = sub.add_parser("shell", help="open the legacy delegation shell")
    sp.set_defaults(func=cmd_shell)

    return p


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
