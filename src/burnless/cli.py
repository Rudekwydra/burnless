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
from dataclasses import dataclass

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
from . import maestro_adapters
from . import dashboard
from . import live_runner
from .estimator import estimate_tokens
from .codec.decoder import normalize_worker_envelope
from .cmd_wrapper import run_and_capsule
from .report_kind import (
    infer_kind_hint as _infer_kind_hint,
    normalize_report_kind as _normalize_report_kind,
)
from . import init_claude_code as _init_claude_code_mod
from . import epochs as epochs_mod
from . import audit_graph
from .prompt_context import (_with_runtime_context, _build_cacheable_runtime_prefix, _TELEGRAPHIC_OUTPUT_HINT, _QTP_F_FIXED_SUFFIX)

from .delegation_parse import (
    parse_chain_from_delegation as _parse_chain_from_delegation,
    parse_tier_from_delegation as _parse_tier_from_delegation,
    parse_created_at_from_delegation as _parse_created_at_from_delegation,
    parse_goal_from_delegation as _parse_goal_from_delegation,
    extract_test_status as _extract_test_status,
    extract_verify_block as _extract_verify_block,
)

from ._pro import audit as _audit_mod
import sys as _sys
from . import plugin_loader as _plugin_loader_parent
_sys.modules.setdefault("burnless._pro.plugin_loader", _plugin_loader_parent)
del _sys, _plugin_loader_parent
_audit_summary_evidence = _audit_mod.audit_summary_evidence
_audit_execution_filesystem = _audit_mod.audit_execution_filesystem

from .exec.runner import (
    execute_delegation, RunOpts, _apply_verify_gate, _load_anthropic_key,
    _record_and_bump, normalize_worker_envelope, _infer_kind_hint, _normalize_report_kind,
    MAESTRO_TIER_MODEL, ANTHROPIC_ENV_PATHS, DEFAULT_MAX_TOKENS,
    _run_with_maestro, _should_use_maestro_backend, _should_use_cached_worker,
    _build_retry_prompt, _build_audit_fix_prompt, _tier_has_multiple_providers,
    _select_provider_cfg, _record_provider_attempt,
)




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


def _hardcore_blocked(
    cfg: dict,
    text: str,
    tier_override: str | None,
    args: argparse.Namespace,
) -> tuple[bool, str, str, str]:
    """Return (blocked, natural_tier, matched_kw, policy_source).

    Thin adapter over routing.decide_route: blocks only when the scored route
    decision is ``blocked`` (policy=block + requested tier above natural route)
    and --force was not passed.
    """
    if not tier_override or getattr(args, "force", False):
        return False, "", "", ""
    decision = routing_mod.decide_route(text, tier_override, cfg.get("routing", {}))
    if decision.action == "blocked":
        return True, decision.natural_tier, decision.matched_keyword or "default", decision.policy_source
    return False, decision.natural_tier, "", decision.policy_source


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

    from . import spec_validator as _spec_validator
    if _spec_validator.uses_deprecated_validation_heading(text):
        print(_spec_validator.format_validation_alias_warning(cfg.get("language", "pt-BR")), file=sys.stderr)
    if _spec_validator.verify_block_is_silent_noop(text):
        print(_spec_validator.format_verify_warning(cfg.get("language", "pt-BR")), file=sys.stderr)
        _enforce_fence = cfg.get("validation", {}).get("enforce_verify_fence", True)
        _allow_unfenced = getattr(args, "allow_unfenced_verify", False)
        if _spec_validator.should_block_unfenced_verify(text, _enforce_fence, _allow_unfenced):
            return 6

    is_blocked, natural_tier, matched_kw, policy_source = _hardcore_blocked(cfg, text, tier_override, args)
    if is_blocked:
        lang = cfg.get("language", "pt-BR")
        print(routing_mod.format_escalation_block(lang, tier_override, natural_tier, matched_kw, policy_source))
        return 5

    if tier_override:
        tier, kw = tier_override, "manual"
        modulation_reason = ""
    else:
        tier, kw = routing_mod.route(text, cfg["routing"])
        modulation_reason = ""
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
    return execute_delegation(RunOpts(
        id=args.id,
        timeout=args.timeout,
        stale_timeout_s=getattr(args, "stale_timeout_s", None),
        dry_run=args.dry_run,
        progress=getattr(args, "progress", None),
        mode=getattr(args, "mode", None),
        cold_cache=getattr(args, "cold_cache", False),
        verbose=getattr(args, "verbose", False),
    ))







def cmd_status(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    state = state_mod.load(p["state"])
    m = metrics_mod.load(p["metrics"])
    print(dashboard.render_status(state, m))
    from .integrity import scan_orphans
    project_root = root.parent
    orphans = scan_orphans(project_root)
    if orphans:
        print(f"⚠ {len(orphans)} delegation(s) ran without a capsule: {', '.join(orphans[:10])}")
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


def cmd_models(args: argparse.Namespace) -> int:
    """View resolved tier→worker table or set a tier worker as the new global default."""
    import yaml

    if getattr(args, "models_action", None) != "set":
        # VIEW mode: print tier→worker mapping
        root = paths_mod.require_root()
        cfg = config_mod.load(paths_mod.paths_for(root)["config"])
        print("tier      provider        model            source")
        for tier in ("diamond", "gold", "silver", "bronze"):
            if tier in cfg.get("agents", {}):
                a = cfg["agents"][tier]
                name = a.get("name", "?")
                prov = a.get("provider", "anthropic")
                default_name = config_mod.DEFAULT_CONFIG.get("agents", {}).get(tier, {}).get("name")
                marker = "(default)" if name == default_name else "(custom)"
                print(f"{tier:<9} {prov:<15} {name:<16} {marker}")
        return 0

    # SET mode: parse spec, build agent, optionally persist
    provider, model = config_mod.parse_worker_spec(args.spec)
    agent = config_mod.build_worker_agent(provider, model)

    if getattr(args, "make_default", False):
        gp = config_mod.global_config_path()
        existing = {}
        if gp.exists():
            existing = yaml.safe_load(gp.read_text(encoding="utf-8")) or {}
        existing.setdefault("agents", {})[args.tier] = agent
        gp.parent.mkdir(parents=True, exist_ok=True)
        gp.write_text(yaml.safe_dump(existing, sort_keys=False, allow_unicode=True))
        print(f"✓ default updated: {args.tier} = {provider}:{model} (written to {gp})")
        return 0
    else:
        print(f"{args.tier} = {provider}:{model} (not persisted). Per-call: burnless do --{args.tier} {provider}:{model}  |  persist: add --default  |  per-chat: /burnless in chat")
        return 0


def cmd_menu(args: argparse.Namespace) -> int:
    from . import menu as menu_mod
    from .config import DEFAULT_CONFIG, build_worker_agent, parse_worker_spec, global_config_path
    import yaml as _yaml
    root = paths_mod.require_root()
    cfg = config_mod.load(paths_mod.paths_for(root)["config"])
    providers = menu_mod.detect_providers()
    import sys as _sys
    if _sys.stdin.isatty() and not getattr(args, "view", False):
        def _persist(tier, spec):
            prov, model = parse_worker_spec(spec)
            agent = build_worker_agent(prov, model)
            gp = global_config_path()
            existing = {}
            if gp.exists():
                existing = _yaml.safe_load(gp.read_text()) or {}
            existing.setdefault("agents", {})[tier] = agent
            gp.parent.mkdir(parents=True, exist_ok=True)
            gp.write_text(_yaml.safe_dump(existing, sort_keys=False, allow_unicode=True))
        menu_mod.run_interactive(cfg, DEFAULT_CONFIG, providers, persist_fn=_persist)
        return 0
    print(menu_mod.build_menu_view(cfg, DEFAULT_CONFIG, providers))
    return 0


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

    if not capsule_path.exists():
        print(f"burnless: no capsule for {args.id} (run it first?)", file=sys.stderr)
        return 2
    print(capsule_path.read_text(encoding="utf-8"))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    delegation_id = None if args.session else args.delegation_id
    records = audit_graph.read_records(root.parent, delegation_id)
    if args.json:
        print(json.dumps(records, indent=2))
    else:
        if args.session:
            from . import audit_stats
            print(audit_stats.render_summary(audit_stats.summarize(records)))
        output = audit_graph.render(records)
        print(output if output else "no audit records")
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
    print(
        "burnless compress is deprecated. The encrypted-capsule compressor (cipher + "
        "key custody) is reserved for burnless Pro / Synapsis; the v1 key_store was "
        "memory-only with no cross-process decode. The live chat uses semantic "
        "compression (no cipher). See capsule burnless-cipher-decoder-deprecated-2026-06-10.",
        file=sys.stderr,
    )
    return 2


def cmd_decode(args: argparse.Namespace) -> int:
    print(
        "burnless decode is deprecated. Cipher capsule decoding (XOR + in-memory key) does "
        "not work across processes and is reserved for burnless Pro / Synapsis. The live "
        "chat decodes semantically via the Maestro decoder_hint (no cipher). See capsule "
        "burnless-cipher-decoder-deprecated-2026-06-10.",
        file=sys.stderr,
    )
    return 2


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
        model = getattr(args, "model", None) or config_mod.DEFAULT_PROVIDER_MODELS["claude"]
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
        codex_model = getattr(args, "model", None) or config_mod.DEFAULT_PROVIDER_MODELS["codex"]
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

    result = dbg.trace(bl_root, args.did, model=args.model or dbg._DEFAULT_MODEL, timeout=args.timeout)
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
        model=args.model or dbg._DEFAULT_MODEL,
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


def _worker_overrides_from_args(args) -> dict:
    """Collect per-call tier worker overrides from --diamond/--gold/--silver/--bronze."""
    return {t: getattr(args, t) for t in ("diamond", "gold", "silver", "bronze") if getattr(args, t, None)}


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
        force=getattr(args, "force", False),
        allow_relative_paths=getattr(args, "allow_relative_paths", False),
        allow_unfenced_verify=getattr(args, "allow_unfenced_verify", False),
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

    _config_patched = False
    _orig_config_text: str | None = None

    # Per-call worker overrides (--diamond/--gold/--silver/--bronze PROVIDER:MODEL),
    # temp-patched into the project config and restored in the finally below.
    _worker_overrides = _worker_overrides_from_args(args)
    if _worker_overrides:
        if _orig_config_text is None:
            _orig_config_text = p["config"].read_text(encoding="utf-8")
        _cfg_w = config_mod.load(p["config"])
        _cfg_w = config_mod.apply_worker_overrides(_cfg_w, _worker_overrides)
        config_mod.save(p["config"], _cfg_w)
        _config_patched = True

    run_args = argparse.Namespace(
        id=did,
        dry_run=False,
        timeout=getattr(args, "timeout", 600) or 600,
        stale_timeout_s=getattr(args, "stale_timeout_s", None),
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


def cmd_route(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    if getattr(args, "explain", False):
        decision = routing_mod.decide_route(args.text, getattr(args, "tier", None), cfg["routing"])
        agent = cfg["agents"].get(decision.effective_tier, {})
        print(routing_mod.format_route_explain(decision, agent.get("name", ""), agent.get("command", "")))
        return 0
    info = routing_mod.explain_route(args.text, cfg["routing"])
    agent = cfg["agents"][info["tier"]]
    print(f"tier:    {info['tier']}")
    print(f"agent:   {agent['name']}  ({agent['command']})")
    print(f"matched: {info['matched_keyword'] or '(default)'}")
    return 0


def cmd_epoch(args: argparse.Namespace) -> int:
    import sys as _sys
    _explicit = getattr(args, "root", None)
    if _explicit:
        root_path = Path(_explicit)
    else:
        cwd = getattr(args, "cwd", None)
        workspace = getattr(args, "workspace", None)
        transcript = getattr(args, "transcript", None)
        if cwd is not None:
            root_path = epochs_mod.resolve_root(cwd, workspace=workspace, transcript=transcript) or Path.cwd()
        else:
            _fr = paths_mod.find_root()
            root_path = (_fr.parent if _fr else Path.cwd())
    chat_id = getattr(args, "chat_id", None)
    epoch_cmd = getattr(args, "epoch_cmd", None)

    if epoch_cmd == "on":
        from .epochs import set_enabled
        set_enabled(root_path, True)
        print("epochs: ON")
        return 0

    elif epoch_cmd == "off":
        from .epochs import set_enabled
        set_enabled(root_path, False)
        print("epochs: OFF")
        return 0

    elif epoch_cmd == "status":
        from .epochs import is_enabled
        state = is_enabled(root_path)
        epochs_base = root_path / ".burnless" / "epochs"
        chats = [d for d in epochs_base.iterdir() if d.is_dir()] if epochs_base.exists() else []
        summaries = sum(len(list(d.glob("*.md"))) for d in chats)
        label = "ON" if state else "OFF"
        print(f"epochs: {label}  ({len(chats)} chats, {summaries} summaries)")
        return 0

    elif epoch_cmd == "capture":
        text = _sys.stdin.read()
        if os.environ.get("BURNLESS_EPOCH_V2"):
            try:
                from . import epochs_v2
                lp = epochs_v2.apply_capture(root_path, chat_id, text)
                if getattr(args, "emit_chain", False):
                    print("> ordem: documento vivo (living-doc v2)\n")
                    print(epochs_v2.living_seed(root_path, chat_id))
                else:
                    print(lp.name)
                return 0
            except Exception as e:
                print(f"warning: epochs_v2 failed ({e}); falling back to v1", file=_sys.stderr)
        summarizer = epochs_mod.epoch_summarizer(root_path)
        s = summarizer(text)
        if s is None:
            print("warning: summarizer failed (fail-open, no mutation)", file=_sys.stderr)
            return 0
        path = epochs_mod.append_epoch(root_path, chat_id, s)
        level = 0
        while epochs_mod.needs_consolidation(root_path, chat_id, level):
            result = epochs_mod.consolidate_level(root_path, chat_id, level, summarizer)
            if result is None:
                break
            level += 1
        if getattr(args, "emit_chain", False):
            # Newest-first: seed.md alimenta o carry-forward, truncado pelo topo
            # no clear-resume. Topo = ultimo checkpoint vivo (ler novo -> velho).
            print("> ordem: mais NOVO primeiro (topo = ultimo checkpoint vivo)\n")
            for f in reversed(epochs_mod.active_chain(root_path, chat_id)):
                print(f"# {f.name}\n")
                print(f.read_text(encoding='utf-8'))
                print()
        else:
            print(path.name)
        return 0

    elif epoch_cmd == "read":
        chain = epochs_mod.active_chain(root_path, chat_id)
        for f in chain:
            print(f"# {f.name}\n")
            print(f.read_text(encoding='utf-8'))
            print()
        return 0

    elif epoch_cmd == "cleanup":
        n = epochs_mod.cleanup_originais(root_path, chat_id)
        print(f"removed {n}")
        return 0

    elif epoch_cmd == "resolve-root":
        cwd = getattr(args, "cwd", None)
        workspace = getattr(args, "workspace", None)
        transcript = getattr(args, "transcript", None)
        r = epochs_mod.resolve_root(cwd, workspace=workspace, transcript=transcript)
        print(str(r) if r else "")
        return 0

    elif epoch_cmd == "resume":
        cwd = getattr(args, "cwd", None)
        workspace = getattr(args, "workspace", None)
        transcript = getattr(args, "transcript", None)
        root = epochs_mod.resolve_root(cwd, workspace=workspace, transcript=transcript)
        if root is None:
            print("")
            return 0
        chain = epochs_mod.carry_forward_chain(root, getattr(args, "chat_id", None))
        print(chain)
        return 0

    return 2













def cmd_economy(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    metrics = metrics_mod.load(p["metrics"])
    from . import economy
    r = economy.compute_economy(metrics, cfg)
    if getattr(args, "json", False):
        # Convert EconomyReport to dict for JSON output
        buckets_dicts = [
            {"name": b.name, "tokens": b.tokens, "usd": b.usd, "note": b.note}
            for b in r.buckets
        ]
        output = {
            "buckets": buckets_dicts,
            "total_tokens": r.total_tokens,
            "total_usd": r.total_usd,
            "assumptions": r.assumptions,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(dashboard.render_economy(r))
    return 0


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


def _event_oneliner(events_list) -> str | None:
    if not events_list:
        return None
    data = events_list[-1].get("data")
    if data is None:
        return None
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError):
        return str(data)


def cmd_session(args: argparse.Namespace) -> int:
    from . import events as events_mod
    from . import scope as scope_mod
    from . import session_hud
    root = paths_mod.find_root()
    if root is None:
        print("burnless: not initialized in this directory tree. run `burnless init` first.", file=sys.stderr)
        return 1

    epochs_on = (root / "epochs.on").exists()
    mode = "rolling" if epochs_on else "default"

    deleg = events_mod.read_events(root, event_type="delegation_completed", limit=1)
    last_status = None
    if deleg:
        d = deleg[-1].get("data")
        if isinstance(d, dict):
            last_status = d.get("status")
        elif isinstance(d, str):
            last_status = d

    savings = None
    turns = None
    try:
        st = state_mod.load(root / "state.json")
        if isinstance(st, dict):
            sv = st.get("savings")
            savings = sv if isinstance(sv, dict) else None
            t = st.get("turns")
            turns = t if isinstance(t, int) else None
    except Exception:
        savings = None
        turns = None

    try:
        scope_hash = scope_mod.stable_project_hash(root.parent)
    except Exception:
        scope_hash = None

    state = {
        "project": str(root.parent),
        "mode": mode,
        "last_status": last_status,
        "savings": savings,
        "scope_hash": scope_hash,
        "turns": turns,
    }

    if getattr(args, "json", False):
        print(json.dumps(state))
        return 0

    cfg = config_mod.load(root / "config.yaml")
    display = cfg.get("display") if isinstance(cfg.get("display"), dict) else {}
    style = display.get("session_hud", "compact") if isinstance(display, dict) else "compact"
    print(session_hud.render_hud(state, style=style))
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    from . import events as events_mod
    from . import session_hud
    root = paths_mod.find_root()
    if root is None:
        print("burnless: not initialized in this directory tree. run `burnless init` first.", file=sys.stderr)
        return 1

    def latest(event_type):
        return _event_oneliner(events_mod.read_events(root, event_type=event_type, limit=1))

    active_mode = latest("mode_changed")
    if active_mode is None:
        active_mode = "rolling" if (root / "epochs.on").exists() else "default"

    sections = {
        "active_mode": active_mode,
        "last_hook_injection": latest("hook_injected"),
        "last_compaction_decision": latest("compaction_decision"),
        "last_route_decision": latest("route_decision"),
        "last_retrieval": latest("retrieve_called"),
        "last_delegation_status": latest("delegation_completed"),
    }
    print(session_hud.render_explain(sections))
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

    from .check_primitives import register_check_parser
    register_check_parser(sub)

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
    sp.add_argument("--no-wire", action="store_true", dest="no_wire",
                    help="(with --claude-code) skip auto-wiring of the UserPromptSubmit hook")
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
        "--allow-relative-paths",
        action="store_true",
        dest="allow_relative_paths",
        help="skip the absolute-path guard (workers run in isolated cwd; relative paths may fail)",
    )
    sp.add_argument(
        "--allow-unfenced-verify",
        action="store_true",
        dest="allow_unfenced_verify",
        help="allow dispatch with a ## Verify section that has no fenced block (gate will not run)",
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

    sp = sub.add_parser("economy", help="show real $ savings split into 4 buckets")
    sp.add_argument("--json", action="store_true", help="emit raw JSON")
    sp.set_defaults(func=cmd_economy)

    sp = sub.add_parser("session", help="show the session HUD")
    sp.add_argument("--json", action="store_true", dest="json", help="emit raw state JSON")
    sp.set_defaults(func=cmd_session)

    sp = sub.add_parser("explain", help="explain the latest state/cost/route decisions")
    sp.add_argument("--last", action="store_true", dest="last", help="focus on the last delegation")
    sp.set_defaults(func=cmd_explain)

    sp = sub.add_parser("providers", help="inspect or reset multi-provider health stats")
    providers_sub = sp.add_subparsers(dest="providers_cmd")
    sp.set_defaults(func=lambda args, parser=sp: parser.print_help() or 0)
    psp = providers_sub.add_parser("stats", help="show provider health stats")
    psp.set_defaults(func=cmd_providers_stats)
    psp = providers_sub.add_parser("reset", help="clear provider health stats")
    psp.set_defaults(func=cmd_providers_reset)

    sp = sub.add_parser("models", help="view or set the tier→worker mapping")
    models_sub = sp.add_subparsers(dest="models_action")
    sp.set_defaults(func=cmd_models)
    msp = models_sub.add_parser("set", help="set a tier worker (add --default to persist to global)")
    msp.add_argument("tier", choices=["diamond", "gold", "silver", "bronze"])
    msp.add_argument("spec", help="provider:model, e.g. ollama:gemma4-e4b or sonnet")
    msp.add_argument("--default", dest="make_default", action="store_true", help="persist as the new global default")
    msp.set_defaults(func=cmd_models)

    sp = sub.add_parser("menu", help="show the tier->worker config view (table + providers + hints)")
    sp.add_argument("--view", action="store_true", help="print the table non-interactively (no prompts)")
    sp.set_defaults(func=cmd_menu)

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

    sp = sub.add_parser("read", help="print compact JSON summary for delegation ID")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("log", help="print raw log for delegation ID")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("capsule", help="show the operational capsule for a delegation")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_capsule)

    sp = sub.add_parser("audit", help="read and render audit graph records")
    sp.add_argument("delegation_id", nargs="?", default=None, help="delegation ID (e.g. d123)")
    sp.add_argument("--session", action="store_true", help="show all records for project")
    sp.add_argument("--json", action="store_true", help="emit raw JSON")
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("watch", help="Stream liveness events from a delegation (.burnless/runs/<did>/liveness.jsonl)")
    sp.add_argument("did", help="delegation ID to watch (e.g. d378)")
    sp.add_argument("--since", type=int, default=0,
                    help="skip first N existing events before streaming")
    sp.add_argument("--no-follow", action="store_true",
                    help="print existing events and exit (do not tail)")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("compress", help="(deprecated) cipher capsule compressor — reserved for Pro/Synapsis")
    sp.add_argument("--file", "-f", default=None)
    sp.add_argument("--level", default=None, choices=["light", "balanced", "extreme"])
    sp.add_argument("--out", "-o", default=None)
    sp.set_defaults(func=cmd_compress)

    sp = sub.add_parser("decode", help="(deprecated) cipher capsule decoder — reserved for Pro/Synapsis")
    sp.add_argument("capsule", nargs="?", default=None)
    sp.add_argument("--capsule", dest="capsule_flag", default=None)
    sp.add_argument("--file", "-f", default=None)
    sp.set_defaults(func=cmd_decode)

    sp = sub.add_parser("route", help="dry-run routing for a piece of text")
    sp.add_argument("text")
    sp.add_argument("--explain", action="store_true", help="show full scored route decision + escalation policy")
    sp.add_argument("--tier", choices=["diamond", "gold", "silver", "bronze"], help="test routing a requested-tier upgrade against the natural route")
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
        "--cold-cache",
        action="store_true",
        dest="cold_cache",
        help="inject a nonce to force cache miss — use for cold-cache benchmarks",
    )
    sp.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="worker timeout in seconds (forwarded to the embedded run)",
    )
    sp.add_argument(
        "--stale-timeout-s",
        type=int,
        default=None,
        dest="stale_timeout_s",
        help="abort if no worker output for N seconds (forwarded to the embedded run)",
    )
    sp.add_argument(
        "--allow-relative-paths",
        action="store_true",
        dest="allow_relative_paths",
        help="skip the absolute-path guard (workers run in isolated cwd; relative paths may fail)",
    )
    sp.add_argument(
        "--allow-unfenced-verify",
        action="store_true",
        dest="allow_unfenced_verify",
        help="allow dispatch with a ## Verify section that has no fenced block (gate will not run)",
    )
    sp.add_argument(
        "--force",
        action="store_true",
        help="override tier escalation policy (forwarded to delegate)",
    )
    for _t in ("diamond", "gold", "silver", "bronze"):
        sp.add_argument(
            f"--{_t}",
            default=None,
            metavar="PROVIDER:MODEL",
            help=f"override the {_t} worker for this run only (e.g. --{_t} ollama:gemma4-e4b). Pair with --tier {_t} to also force routing.",
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

    sp = sub.add_parser("trace", help="GoPro-trace a delegation via local ollama (Debugless)",
                        description="GoPro-trace a delegation via local ollama (Debugless)")
    sp.add_argument("did", help="delegation ID to trace (e.g. d378)")
    sp.add_argument("--model", default=None, help="ollama model to use (default: bronze gemma-4 local)")
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
    dsp.add_argument("--model", default=None, help="ollama model to use (default: bronze gemma-4 local)")
    dsp.set_defaults(func=cmd_debugless_sweep)

    sp = sub.add_parser("epoch", help="rolling-memory epoch engine (capture/read/cleanup/on/off/status)")
    epoch_common = argparse.ArgumentParser(add_help=False)
    epoch_common.add_argument("--chat-id", required=False, default=None, dest="chat_id", help="chat ID for epoch storage (required for capture/read/cleanup)")
    epoch_common.add_argument("--root", default=None, help="project root (default: find_root())")
    epoch_common.add_argument("--cwd", default=None, help="working directory for root resolution")
    epoch_common.add_argument("--workspace", default=None, help="workspace root for project detection")
    epoch_common.add_argument("--transcript", default=None, help="transcript file path for project detection")
    epoch_sub = sp.add_subparsers(dest="epoch_cmd", required=True)
    esp = epoch_sub.add_parser("capture", parents=[epoch_common], help="read STDIN, summarize, append, consolidate")
    esp.add_argument("--emit-chain", action="store_true", dest="emit_chain", default=False,
                     help="on successful append, print the active chain to stdout instead of the slot name")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="capture")
    esp = epoch_sub.add_parser("read", parents=[epoch_common], help="print active chain to stdout")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="read")
    esp = epoch_sub.add_parser("cleanup", parents=[epoch_common], help="remove originais directory")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="cleanup")
    esp = epoch_sub.add_parser("on", parents=[epoch_common], help="enable rolling memory (create marker file)")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="on")
    esp = epoch_sub.add_parser("off", parents=[epoch_common], help="disable rolling memory (remove marker file)")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="off")
    esp = epoch_sub.add_parser("status", parents=[epoch_common], help="show ON/OFF state + chat/summary count")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="status")
    esp = epoch_sub.add_parser("resolve-root", parents=[epoch_common], help="resolve project root from cwd")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="resolve-root")
    esp = epoch_sub.add_parser("resume", parents=[epoch_common], help="emit carry-forward chain")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="resume")

    sp = sub.add_parser("doctor", help="healthcheck install/config/wiring/MCP; --fix auto-remediates safe issues")
    sp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sp.add_argument("--fix", action="store_true",
                    help="auto-remediate safe issues (write config, wire hooks, copy managed files, register MCP) then re-check")
    sp.set_defaults(func=cmd_doctor)

    return p


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import doctor as doctor_mod
    checks = doctor_mod.run_checks(fix=bool(getattr(args, "fix", False)))
    if getattr(args, "json", False):
        print(json.dumps(doctor_mod.render_json(checks), indent=2, ensure_ascii=False))
    else:
        print(doctor_mod.render_human(checks))
    return doctor_mod.exit_code(checks)


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
        parser = build_parser()
        parser.print_help()
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, 'profile', None):
        os.environ['BURNLESS_PROFILE'] = args.profile
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
