from __future__ import annotations
import argparse
import os
import sys
import json
import re
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

from .. import paths as paths_mod
from .. import config as config_mod
from .. import state as state_mod
from .. import metrics as metrics_mod
from .. import delegations as deleg_mod
from .. import compression as compression_mod
from .. import lifetime as lifetime_mod
from .. import agents as agents_mod
from .. import live_runner
from .. import dashboard
from .. import savings_footer as savings_footer_mod
from ..estimator import estimate_tokens
from ..codec.decoder import normalize_worker_envelope
from ..report_kind import (
    infer_kind_hint as _infer_kind_hint,
    normalize_report_kind as _normalize_report_kind,
)
from ..delegation_parse import (
    parse_chain_from_delegation as _parse_chain_from_delegation,
    parse_tier_from_delegation as _parse_tier_from_delegation,
    parse_created_at_from_delegation as _parse_created_at_from_delegation,
    parse_goal_from_delegation as _parse_goal_from_delegation,
    extract_test_status as _extract_test_status,
    extract_verify_block as _extract_verify_block,
)
from ..prompt_context import (
    _with_runtime_context,
    _build_cacheable_runtime_prefix,
    _TELEGRAPHIC_OUTPUT_HINT,
    _QTP_F_FIXED_SUFFIX,
)

def _build_retry_prompt(original: str, did: str, status: str, summary: dict) -> str:
    issues = summary.get("issues") or []
    issues_str = ", ".join(str(i) for i in issues) if issues else "(none)"
    return f"{original}\n\n[RETRY {did} prev={status}] Issues: {issues_str}"


def _build_audit_fix_prompt(original: str, did: str, audit: dict) -> str:
    issues = audit.get("issues") or []
    audit_summary = audit.get("summary") or ""
    issues_str = ", ".join(str(i) for i in issues) if issues else "(none)"
    return f"{original}\n\n[AUDIT FIX {did}] Issues: {issues_str}. Audit: {audit_summary}"

DEFAULT_MAX_TOKENS = 4096

MAESTRO_TIER_MODEL = dict(config_mod.DEFAULT_TIER_MODELS)
ANTHROPIC_ENV_PATHS = (
    Path.home() / ".config" / "burnless" / "anthropic.env",
)


@dataclass
class RunOpts:
    id: str
    timeout: int | None = None
    stale_timeout_s: int | None = None
    dry_run: bool = False
    progress: str | None = None
    mode: str | None = None
    cold_cache: bool = False
    verbose: bool = False


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
        from .. import maestro_legacy as maestro_mod
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
    from .. import cached_worker as _cw
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

def _apply_verify_gate(
    summary: dict,
    verify_cmds: list[str],
    *,
    cwd,
    did: str,
    log_path,
    timeout: int,
) -> dict:
    """Re-execute the spec's ## Verify commands after the worker exits.

    No-op if verify_cmds is empty or summary status is not OK.
    May only demote OK→PART; never promotes any status.
    Full stdout/stderr appended to log_path; only short tail enters summary.
    """
    if not verify_cmds or summary.get("status") != "OK":
        return summary
    verify_log = "\n--- VERIFY ---\n"
    passed = 0
    for cmd in verify_cmds:
        try:
            r = subprocess.run(
                cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout
            )
            verify_log += f"$ {cmd}\n{r.stdout}{r.stderr}\n"
            if r.returncode != 0:
                out_tail = ((r.stdout or "") + (r.stderr or "")).strip()[-500:]
                with open(log_path, "a", encoding="utf-8") as _lf:
                    _lf.write(verify_log)
                summary = dict(summary)
                summary["status"] = "PART"
                issues = list(summary.get("issues") or [])
                issues.append(f"verify_failed: {cmd} (rc={r.returncode}): {out_tail}")
                summary["issues"] = issues
                summary["next"] = cmd
                return summary
            passed += 1
        except subprocess.TimeoutExpired:
            verify_log += f"$ {cmd}\n(timeout after {timeout}s)\n"
            with open(log_path, "a", encoding="utf-8") as _lf:
                _lf.write(verify_log)
            summary = dict(summary)
            summary["status"] = "PART"
            issues = list(summary.get("issues") or [])
            issues.append(f"verify_failed: {cmd} (rc=timeout): timed out after {timeout}s")
            summary["issues"] = issues
            summary["next"] = cmd
            return summary
    verify_log += f"(all {passed} checks passed)\n"
    with open(log_path, "a", encoding="utf-8") as _lf:
        _lf.write(verify_log)
    summary = dict(summary)
    validated = list(summary.get("validated") or [])
    validated.append(f"verify: {passed}/{len(verify_cmds)} checks passed")
    summary["validated"] = validated
    return summary


def _preflight_verify_block(verify_cmds, *, cwd, timeout=30):
    """Run each ## Verify command against the PRE-change state to catch a
    MALFORMED check (one that crashes rather than cleanly exiting 1).

    Returns a list of human-readable complaints, one per malformed check;
    an empty list means every check is well-formed. A clean exit-1 (the
    desired state simply does not hold yet, pre-change) is NOT malformed.
    File-not-yet-created ('No such file or directory') is expected pre-change
    and is deliberately ignored.
    """
    _CRASH_SIGS = (
        "command not found",
        "SyntaxError",
        "syntax error",
        "unexpected EOF",
        "unbound variable",
        "jq: error",
    )
    complaints = []
    for cmd in verify_cmds:
        try:
            r = subprocess.run(
                cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            continue
        out = (r.stdout or "") + (r.stderr or "")
        malformed = r.returncode in (126, 127) or any(s in out for s in _CRASH_SIGS)
        if malformed:
            complaints.append(f"{cmd} (rc={r.returncode}): {out.strip()[:200]}")
    return complaints


def _verify_badge(summary: dict) -> str:
    """Distinguish a runner-verified OK from a worker-claimed OK using the
    deterministic 'verify: N/N checks passed' marker in summary['validated']."""
    if str(summary.get("status") or "").upper() != "OK":
        return ""
    import re as _re
    for item in (summary.get("validated") or []):
        m = _re.search(r"verify:\s*(\d+)\s*/\s*(\d+)\s*checks passed", str(item))
        if m:
            return f"✓ runner-verified ({m.group(1)}/{m.group(2)})"
    return "⚠ unverified — no ## Verify gate ran (worker-claimed OK)"

def execute_delegation(opts: RunOpts, root=None) -> int:
    root = root or paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    state = state_mod.load(p["state"])
    metrics = metrics_mod.load(p["metrics"])
    metrics_mod.bump_legacy_counter(p["metrics"], "legacy_run_calls")
    did = opts.id
    deleg_path = p["delegations"] / f"{did}.md"
    if not deleg_path.exists():
        print(f"burnless: delegation {did} not found at {deleg_path}", file=sys.stderr)
        return 2
    deleg_text = deleg_path.read_text(encoding="utf-8")
    _verify_cmds = _extract_verify_block(deleg_text) if cfg.get("validation", {}).get("honest_exit_code", True) else []
    if _verify_cmds and cfg.get("validation", {}).get("preflight_verify", True):
        _pf = _preflight_verify_block(
            _verify_cmds, cwd=root.parent,
            timeout=cfg.get("validation", {}).get("verify_timeout_s", 120),
        )
        if _pf:
            print(f"burnless: {did} ABORTED — malformed ## Verify check(s):", file=sys.stderr)
            for _c in _pf:
                print(f"  x {_c}", file=sys.stderr)
            print("  fix the check (it crashes instead of cleanly exiting 1) and re-dispatch.", file=sys.stderr)
            return 5
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

    if opts.dry_run:
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
    progress_arg = opts.progress
    if progress_arg:
        run_mode = progress_arg
    else:
        legacy_mode = opts.mode
        if legacy_mode and legacy_mode != "plain":
            run_mode = legacy_mode
        else:
            display_cfg = cfg.get("display", {}).get("progress_detail", "brief")
            run_mode = display_cfg if display_cfg in {"minimal", "brief", "full", "watch", "quiet", "plain"} else "brief"
    from burnless.config import resolve_stale_timeout
    stale_timeout_s = resolve_stale_timeout(cfg, tier, opts.stale_timeout_s, provider=selected_provider)

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
    use_maestro = (not multi_provider) and _should_use_maestro_backend(opts, cfg, tier)
    use_cached_worker = (not multi_provider) and (not use_maestro) and _should_use_cached_worker(opts, cfg, tier, api_key)
    result: dict | None = None
    backend_used = "subprocess"
    if use_maestro:
        result = _run_with_maestro(
            p, did=did, tier=tier, agent_cfg=selected_agent_cfg, prompt=prompt, log_path=log_path,
        )
        if result is not None:
            backend_used = "maestro"
            if sys.stdout.isatty() or opts.verbose:
                print(f"Running {did} with maestro/{tier} ({result['command'][1]})...")

    if result is None and use_cached_worker:
        from .. import cached_worker as _cw
        model = MAESTRO_TIER_MODEL.get(tier, MAESTRO_TIER_MODEL["silver"])
        if sys.stdout.isatty() or opts.verbose:
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
                cold_cache=opts.cold_cache,
            )
            backend_used = "cached_worker"
        except Exception as e:
            print(f"CachedWorker failed ({e}); falling back to subprocess.", file=sys.stderr)

    if result is None:
        from ..ollama_worker import is_ollama_tools_agent as _is_ollama_agent
        if _is_ollama_agent(selected_agent_cfg):
            # Ollama tool-workers have no CLI command, so the live-panel runner
            # cannot drive them. Use the plain agent runner (it dispatches the
            # ollama HTTP tool loop). Avoids a noisy AgentError + false escalation.
            if sys.stdout.isatty() or opts.verbose:
                print(f"Running {did} with {tier}/{selected_agent_cfg['name']}...")
            result = agents_mod.run(selected_agent_cfg, prompt, timeout=opts.timeout, cwd=root.parent)
            deleg_mod.write_log(log_path, result)

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
                timeout=opts.timeout,
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
            if sys.stdout.isatty() or opts.verbose:
                print(f"Running {did} with {tier}/{selected_agent_cfg['name']}...")
            result = agents_mod.run(selected_agent_cfg, prompt, timeout=opts.timeout, cwd=root.parent)
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
                timeout=opts.timeout,
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
            fallback_result = agents_mod.run(fallback_cfg, prompt, timeout=opts.timeout, cwd=root.parent, tier=tier)
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
                    agent_cfg, _full_push_prompt, timeout=opts.timeout, cwd=root.parent
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
                _rescue_timeout = min(int(opts.timeout or 300), 300)
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

    # ── Honest exit code gate (call site A) ─────────────────────────────────
    summary = _apply_verify_gate(
        summary, _verify_cmds,
        cwd=root.parent, did=did, log_path=log_path,
        timeout=cfg.get("validation", {}).get("verify_timeout_s", 120),
    )

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
                _retry_timeout = min(stale_timeout_s * 2, int(opts.timeout or stale_timeout_s * 2))
            else:
                _retry_timeout = int(opts.timeout or 600)

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
                # Honest exit code gate (call site B) — re-verify before accepting
                _r_sum = _apply_verify_gate(
                    _r_sum, _verify_cmds,
                    cwd=root.parent, did=did, log_path=log_path,
                    timeout=cfg.get("validation", {}).get("verify_timeout_s", 120),
                )
                _new_status = str(_r_sum.get("status") or "").upper()
                if _new_status == "OK":
                    summary = _r_sum
                    stale = _r_stale
                    interrupted = _r_interrupted
                    break
                # gate demoted: fall through to PART merge path below

            _orig_issues = summary.get("issues") or []
            _r_issues = _r_sum.get("issues") or []
            summary = _r_sum
            summary["issues"] = list({(_it if isinstance(_it, str) else repr(_it)): _it for _it in (_orig_issues + _r_issues)}.values())
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
    # Increment turn counter for savings footer tracking
    state["turn_counter"] = int(state.get("turn_counter", 0) or 0) + 1
    state_mod.save(p["state"], state)

    # Short output — details via `burnless read/log/capsule/metrics`
    # Default = single-line machine-parseable status (avoids polluting maestro
    # session history). Verbose (3-line summary+reason) opt-in via --verbose
    # or auto-on for TTY humans.
    status_str = summary.get("status", "?")
    verbose = bool(opts.verbose) or sys.stdout.isatty()
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
            _badge = _verify_badge(summary)
            if _badge:
                head = f"{head}\n{_badge}"
        print(head)

        # Savings footer: display token counts and cost breakdown
        if cfg.get("display", {}).get("display_savings_footer", False):
            try:
                turn_num = int(state.get("turn_counter", 1) or 1)
                # Map tier to pricing family (e.g., gold → opus, silver → sonnet, bronze → haiku)
                tier_to_family = {"gold": "opus", "silver": "sonnet", "bronze": "haiku"}
                pricing_model = tier_to_family.get(tier, "opus")
                metrics_obj = savings_footer_mod.metrics_from_savings(
                    savings, pricing_model, turn_num
                )
                footer_text = savings_footer_mod.render_footer(metrics_obj)
                print(f"⚡ {footer_text}")
                savings_footer_mod.log_turn_metrics(metrics_obj, burnless_root=root)
            except Exception as e:
                if verbose:
                    print(f"[savings-footer] error: {e}", file=sys.stderr)
    try:
        from ..integrity import check_run_integrity
        _gapless_warns = check_run_integrity(did, root.parent)
        for _w in _gapless_warns:
            print(f"[gapless] WARN {did}: {_w}", file=sys.stderr)
    except Exception:
        pass
    return 0 if status_str == "OK" else 1

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

