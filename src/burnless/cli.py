from __future__ import annotations
import argparse
import json
import os
import signal
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, replace

from . import __version__, TAGLINE
from . import config as config_mod
from . import i18n as i18n_mod
from . import state as state_mod
from . import metrics as metrics_mod
from . import paths as paths_mod
from . import routing as routing_mod
from . import agents as agents_mod
from . import delegations as deleg_mod
from . import pure_ask as pure_ask_mod
from . import providers as providers_mod
from .coreconfig import resolver as coreconfig_resolver
from .providers.contracts import AskRequest
from . import compression as compression_mod
from . import lifetime as lifetime_mod
from . import claude_integration
from . import codex_integration
from . import provider_autodetect
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
from . import recovery as recovery_mod
from . import transcript_sources as transcript_sources_mod
from . import chat as chat_mod
from . import audit_graph
from . import retrieve as retrieve_mod
from . import events as events_mod
from .pilot import discover_hosts as pilot_discover_hosts
from .pilot import build_report as pilot_build_report
from .pilot import append_session_log as pilot_append_session_log
from .pilot import summarize_session_log as pilot_summarize_session_log
from .pilot import monitor_rollover_loop as pilot_monitor_rollover_loop
from .pilot import resolve_host_adapter as pilot_resolve_host_adapter
from .pilot import run_pilot as pilot_run
from .pilot import hud as hud_mod
from .pilot import append_event as pilot_append_event
from .pilot.cadence_providers import build_cadence_controller
from .pilot.cadence_controller import build_injector as _build_cadence_injector
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
    if epochs_mod.promote_orphan_store(cwd, cwd):
        print("Rolling memory: promoted orphan store from ~/.burnless/orphans into this project.")
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


def cmd_delegate(args: argparse.Namespace, cfg_override: dict | None = None) -> int:
    if os.environ.get("BURNLESS_WORKER") == "1" and os.environ.get("BURNLESS_ALLOW_NESTED") != "1":
        lang = os.environ.get("BURNLESS_LANG", "en")
        msg_text = i18n_mod.msg("guard_nested_delegation", lang)
        print(msg_text, file=sys.stderr)
        return 7
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    # cfg_override lets cmd_do render the delegation against the EFFECTIVE
    # (already worker-overridden) config, so the "- **Agent:**" line matches
    # what will actually run instead of the pre-override tier default
    # (2026-07-02 audit finding #10).
    cfg = cfg_override if cfg_override is not None else config_mod.load(p["config"])
    metrics = metrics_mod.load(p["metrics"])
    text = args.text
    tier_override = args.tier

    from . import spec_validator as _spec_validator
    if _spec_validator.uses_deprecated_validation_heading(text):
        print(_spec_validator.format_validation_alias_warning(cfg.get("language", "pt-BR")), file=sys.stderr)
    if _spec_validator.verify_block_is_silent_noop(text):
        print(_spec_validator.format_verify_warning(cfg.get("language", "pt-BR")), file=sys.stderr)

    allow_rel = getattr(args, "allow_relative_paths", False)
    allow_unfenced = getattr(args, "allow_unfenced_verify", False)
    gate = _spec_validator.evaluate_spec_gates(
        text,
        cfg,
        root.parent,
        allow_relative_paths=allow_rel,
        allow_unfenced_verify=allow_unfenced
    )
    if gate.autofix_notice:
        print(gate.autofix_notice, file=sys.stderr)
    if not gate.ok:
        print(gate.message, file=sys.stderr)
        return 6
    text = gate.text

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
    _project_root_line = f"project_root: {p['root'].parent}"
    if chain:
        header = f"---\n{_project_root_line}\nchain: [{', '.join(chain)}]\n---\n"
    else:
        header = f"---\n{_project_root_line}\n---\n"
    deleg_mod.write_delegation(deleg_path, header + body)
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
        worker_overrides=getattr(args, "worker_overrides", None),
        maestro=getattr(args, "maestro", False),
        no_maestro=getattr(args, "no_maestro", False),
        no_cache_worker=getattr(args, "no_cache_worker", False),
    ))







def _ledger_snapshot(p: dict):
    from . import ledger_projector
    return ledger_projector.project(ledger_projector.read_ledger(p["audit"]))


def _metrics_with_ledger_totals(p: dict) -> dict:
    """metrics.json merged with the ledger-derived authoritative totals, so status/metrics/economy
    reconcile on the same window. Legacy-only scalars (legacy_run_calls, keepalive_*, compression
    ratio, session_snapshots) still come from metrics.json — they have no ledger equivalent yet."""
    m = metrics_mod.load(p["metrics"])
    snap = _ledger_snapshot(p)
    m["burnless_tokens"] = int(snap.accounted_total_tokens)
    m["by_source"] = dict(snap.by_source)
    m["encoder_calls"] = int(snap.encoder_calls)
    m["decoder_calls"] = int(snap.decoder_calls)
    m["brain_calls"] = int(snap.brain_calls)
    m["estimated_cost_avoided_usd"] = round(float(snap.saving_usd), 4)
    return m


def cmd_status(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    state = state_mod.load(p["state"])
    m = _metrics_with_ledger_totals(p)
    print(dashboard.render_status(state, m))
    from .integrity import scan_orphans
    project_root = root.parent
    orphans = scan_orphans(project_root)
    if orphans:
        print(f"⚠ {len(orphans)} delegation(s) ran without a capsule: {', '.join(orphans[:10])}")
    try:
        all_chains = recovery_mod.list_chains(root, host="claude")
    except Exception:
        all_chains = []
    show_all_chains = getattr(args, "show_all_chains", False)
    chains = all_chains if show_all_chains else [c for c in all_chains if c.get("state") == "active"]
    if chains:
        print("")
        print("chains:")
        for c in chains:
            chain_state = c["state"]
            focus = c.get("focus_line") or "(sem foco registrado)"
            print(f"  {c['chain_id']} · pid={c['pid']} ({chain_state}) · last_seen={c['last_seen']} · gen={c['generation']} · {focus}")
    hidden_count = len(all_chains) - len(chains)
    if hidden_count > 0:
        print(f"  ({hidden_count} chain(s) hidden — not active; use --all to see them)")
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    result = recovery_mod.gc_dead_chains(root, host=getattr(args, "host", "claude") or "claude", dry_run=getattr(args, "dry_run", False))
    print(json.dumps(result, ensure_ascii=False))
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

    if getattr(args, "explain", False):
        limit = int(getattr(args, "limit", 50) or 50)
        audit_entries = metrics_mod.read_audit(p["audit"], limit=limit)
        spend_entries = metrics_mod.read_spend(p["root"] / "spend.jsonl" if "root" in p else root / "spend.jsonl", limit=limit)
        print("audit.jsonl")
        print(dashboard.render_audit(audit_entries))
        print()
        print("spend.jsonl")
        if not spend_entries:
            print("(no spend entries yet)")
        else:
            for row in spend_entries:
                usage = row.get("usage") or {}
                bits = ", ".join(
                    f"{k}={int(usage.get(k, 0) or 0)}"
                    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                )
                print(
                    f"{row.get('ts', '')[:19].replace('T', ' ')}  "
                    f"{(row.get('tier') or '-'):<8}  "
                    f"{(row.get('provider') or '-'):<12}  "
                    f"{(row.get('model') or '-'):<18}  "
                    f"{bits}"
                )
        return 0

    if getattr(args, "diff", False):
        diff = metrics_mod.session_diff(p["metrics"])
        print(dashboard.render_session_diff(diff))
        return 0

    m = _metrics_with_ledger_totals(p)
    show_cost = bool(cfg.get("metrics", {}).get("show_estimated_cost", True))
    print(dashboard.render_metrics(m, show_cost=show_cost))
    return 0


def cmd_metrics_migrate(args: argparse.Namespace) -> int:
    from . import ledger_migrate
    root = paths_mod.require_root()
    project_root = root.parent if root.name == ".burnless" else root
    result = ledger_migrate.migrate(project_root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
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


def cmd_retrieve(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    if cfg.get("privacy", {}).get("raw_retention") == "none":
        print(json.dumps({"error": "raw_retention_disabled", "capsule_available": True}))
        return 0
    results = retrieve_mod.search(
        root,
        query=args.query,
        file=args.file,
        entity=args.entity,
        delegation_id=args.id,
    )
    events_mod.append_event(
        root,
        "retrieve_called",
        {
            "id": args.id,
            "query": args.query,
            "file": args.file,
            "entity": args.entity,
            "count": len(results),
        },
        actor="cli",
    )
    if args.json:
        output = {
            "count": len(results),
            "results": [
                {
                    **rec,
                    "snippet": retrieve_mod.snippet(root, rec["ref_id"], max_chars=4000, full=args.full),
                }
                for rec in results
            ],
        }
        print(json.dumps(output, ensure_ascii=False))
    else:
        if not results:
            print("no matches")
        else:
            for rec in results:
                ref_id = rec.get("ref_id", "?")
                kind = rec.get("kind", "?")
                status = rec.get("status", "?")
                snippet_text = retrieve_mod.snippet(root, ref_id, max_chars=4000, full=args.full)
                print(f"{ref_id} [{kind}] {status}")
                for line in snippet_text.split("\n"):
                    print(f"  {line}")
    return 0


def cmd_search_capsules(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    results = retrieve_mod.search(root, query=args.query)
    results = [r for r in results if r.get("kind") == "capsule"]
    results = results[:args.limit]
    events_mod.append_event(
        root,
        "retrieve_called",
        {"search_capsules": args.query, "count": len(results)},
        actor="cli",
    )
    if args.json:
        print(json.dumps({"count": len(results), "results": results}, ensure_ascii=False))
    else:
        if not results:
            print("no matches")
        else:
            for rec in results:
                capsule_id = rec.get("capsule_id", "?")
                capsule_ref = rec.get("capsule_ref", "?")
                print(f"{capsule_id} -> {capsule_ref}")
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
            spend_rows = metrics_mod.read_spend(root / "spend.jsonl")
            if spend_rows:
                by_tier: dict[str, int] = {}
                by_provider: dict[str, int] = {}
                by_model: dict[str, int] = {}
                for row in spend_rows:
                    usage = row.get("usage") or {}
                    total = int(
                        (usage.get("input_tokens") or 0)
                        + (usage.get("output_tokens") or 0)
                        + (usage.get("cache_read_input_tokens") or 0)
                        + (usage.get("cache_creation_input_tokens") or 0)
                    )
                    tier = str(row.get("tier") or "unknown")
                    provider = str(row.get("provider") or "unknown")
                    model = str(row.get("model") or "unknown")
                    by_tier[tier] = by_tier.get(tier, 0) + total
                    by_provider[provider] = by_provider.get(provider, 0) + total
                    by_model[model] = by_model.get(model, 0) + total
                print()
                print("usage real:")
                print(f"  entries: {len(spend_rows)}")
                print("  by tier:")
                for tier, total in sorted(by_tier.items(), key=lambda kv: -kv[1]):
                    print(f"    {tier:<12} {total:>12,}")
                print("  by provider:")
                for provider, total in sorted(by_provider.items(), key=lambda kv: -kv[1]):
                    print(f"    {provider:<12} {total:>12,}")
                print("  by model:")
                for model, total in sorted(by_model.items(), key=lambda kv: -kv[1]):
                    print(f"    {model:<18} {total:>12,}")
        output = audit_graph.render(records)
        print(output if output else "no audit records")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from . import liveness as _live
    bl_root = paths_mod.find_root()
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


def cmd_warm_init(args: argparse.Namespace) -> int:
    bl_root = paths_mod.find_root()
    if bl_root is None:
        print("burnless: no .burnless/ directory found. Run `burnless init` first.", file=sys.stderr)
        return 2
    provider = getattr(args, "provider", "both")
    generic_model = getattr(args, "model", None)
    claude_model_arg = getattr(args, "claude_model", None)
    codex_model_arg = getattr(args, "codex_model", None)
    # --model applies to a single CLI's model id; with --provider both it
    # would silently feed the SAME id to claude and codex (e.g. --model
    # gpt-5.5 tries to init claude with a codex model id, and vice versa
    # with --model claude-sonnet-4-6). Require the per-provider flags
    # instead when both pools are being initialized (2026-07-02 audit
    # finding #11).
    if provider == "both" and generic_model and not (claude_model_arg or codex_model_arg):
        print(
            "burnless: --model is ambiguous with --provider both (it would apply the same "
            "model id to both claude and codex). Use --claude-model/--codex-model, or run "
            "--provider claude / --provider codex separately.",
            file=sys.stderr,
        )
        return 2
    results = {}
    if provider in ("claude", "both"):
        from . import warm_session as ws_claude
        model = claude_model_arg or (generic_model if provider == "claude" else None) \
            or config_mod.DEFAULT_PROVIDER_MODELS["claude"]
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
        codex_model = codex_model_arg or (generic_model if provider == "codex" else None) \
            or config_mod.DEFAULT_PROVIDER_MODELS["codex"]
        print(f"burnless warm init [codex]: seeding warm session for {bl_root.parent} (model={codex_model})...")
        try:
            state = ws_codex.init(bl_root, model=codex_model)
            results["codex"] = state
            cached = state.get("init_usage", {}).get("cached", 0)
            print(f"  codex warm initialized: uuid={state['uuid'][:8]}…  cached_input={cached}")
        except Exception as e:
            print(f"  codex warm init FAILED: {e}", file=sys.stderr)
    return 0 if results else 1


def _print_warm_status_block(provider: str, status_result: dict) -> None:
    """Print one provider's `burnless warm status` block.

    status_result is either a single-model status dict (has an 'exists' key
    directly) or the multi-model shape {model: status_dict} that
    warm_session[.codex].status(model=None) returns for the per-(provider,
    model) warm pools. The old code assumed only the single-model shape and
    checked status_result.get('exists') directly, which is never present on
    the multi-model dict — so it always printed NOT INITIALIZED even when
    warm files existed (2026-07-02 audit finding #7). Mirrors
    cmd_warm_explain(), which already handled both shapes correctly.
    """
    if "exists" in status_result:
        models = {status_result.get("model", "?"): status_result}
    else:
        models = status_result
    any_exists = False
    for model, s in (models or {}).items():
        if not isinstance(s, dict) or not s.get("exists"):
            continue
        any_exists = True
        print(f"warm session [{provider}/{model}] for {s.get('project_root')}:")
        print(f"  uuid:             {s.get('uuid')}")
        print(f"  alive:            {s.get('alive')}")
        print(f"  needs_refresh:    {s.get('needs_refresh')}")
        if "age_minutes" in s:
            print(f"  age_minutes:      {s.get('age_minutes')}")
        if "age_s" in s:
            print(f"  age_s:            {s.get('age_s')}")
        if "last_cache_ratio" in s:
            print(f"  last_cache_ratio: {s.get('last_cache_ratio')}")
        print(f"  created_at:       {s.get('created_at')}")
        print(f"  last_used:        {s.get('last_used')}")
    if not any_exists:
        print(f"warm session [{provider}]: NOT INITIALIZED. Run `burnless warm init --provider {provider}`.")


def cmd_warm_status(args: argparse.Namespace) -> int:
    bl_root = paths_mod.find_root()
    if bl_root is None:
        print("burnless: no .burnless/ directory found.", file=sys.stderr)
        return 2
    provider = getattr(args, "provider", "both")
    if provider in ("claude", "both"):
        from . import warm_session as ws
        _print_warm_status_block("claude", ws.status(bl_root))
    if provider in ("codex", "both"):
        from . import warm_session_codex as ws_codex
        _print_warm_status_block("codex", ws_codex.status(bl_root))
    return 0


def cmd_warm_explain(args):
    bl_root = paths_mod.find_root()
    if bl_root is None:
        print("burnless: no .burnless/ directory found.", file=sys.stderr)
        return 2
    provider = getattr(args, "provider", "both")
    result = {}
    if provider in ("claude", "both"):
        from . import warm_session as ws
        result["claude"] = ws.explain(bl_root)
    if provider in ("codex", "both"):
        from . import warm_session_codex as ws_codex
        result["codex"] = ws_codex.explain(bl_root)
    # Record one warm_session_status event summarizing what we observed.
    try:
        summary = {}
        for prov, models in result.items():
            if isinstance(models, dict) and "exists" in models:
                summary[prov] = {"exists": models.get("exists")}
            elif isinstance(models, dict):
                summary[prov] = {m: d.get("ttl_status") for m, d in models.items() if isinstance(d, dict)}
        events_mod.append_event(bl_root, "warm_session_status", {"provider": provider, "summary": summary})
    except Exception:
        pass
    if getattr(args, "json", False):
        import json as _json
        print(_json.dumps(result, indent=2, default=str))
        return 0
    for prov, models in result.items():
        if isinstance(models, dict) and "exists" in models and not models.get("exists"):
            print(f"warm session [{prov}]: NOT INITIALIZED.")
            continue
        if isinstance(models, dict) and "exists" in models:
            models = {models.get("model", "?"): models}
        for model, d in (models or {}).items():
            if not isinstance(d, dict):
                continue
            print(f"warm session [{prov}/{model}]:")
            print(f"  project_root:       {d.get('project_root')}")
            print(f"  uuid_prefix:        {d.get('uuid_prefix')}")
            print(f"  alive:              {d.get('alive')}")
            print(f"  needs_refresh:      {d.get('needs_refresh')}")
            print(f"  ttl_status:         {d.get('ttl_status')}")
            print(f"  ttl_remaining_min:  {d.get('ttl_remaining_min')}")
            print(f"  cache_read:         {d.get('cache_read')}")
            print(f"  cache_write:        {d.get('cache_write')}")
            print(f"  compaction_caution: {d.get('compaction_caution')}")
    return 0


def cmd_warm_refresh(args: argparse.Namespace) -> int:
    bl_root = paths_mod.find_root()
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

    bl_root = paths_mod.find_root()
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

    bl_root = paths_mod.find_root()
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

    bl_root = paths_mod.find_root()
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
    if getattr(args, "codex", False):
        return _cmd_setup_codex(args)
    from . import setup_wizard
    return setup_wizard.run(
        non_interactive=bool(getattr(args, "non_interactive", False)),
        accept_all=bool(getattr(args, "yes", False)),
        project=getattr(args, "project", None),
    )


def _cmd_setup_codex(args: argparse.Namespace) -> int:
    """Install/update the managed Burnless block in ~/.codex/AGENTS.md.

    HOME-level, not per-project (unlike CLAUDE.md) — Codex's AGENTS.md is a
    single global file, so this never touches the current project's tree.
    """
    try:
        from . import __version__ as _v
    except ImportError:
        _v = "0.7.4"

    agents_md = Path.home() / ".codex" / "AGENTS.md"

    if getattr(args, "dry_run", False):
        if agents_md.exists():
            current = agents_md.read_text(encoding="utf-8")
            existing_block_match = codex_integration.BLOCK_PATTERN.search(current)
            if existing_block_match:
                print(f"AGENTS.md dry-run: existing burnless block found at {agents_md}")
                print("current block:\n" + existing_block_match.group(0))
            else:
                print(f"AGENTS.md dry-run: no burnless block found at {agents_md} — would be appended")
        else:
            print(f"AGENTS.md dry-run: {agents_md} does not exist — would be created")
        print("\nwould become:\n" + codex_integration.render_block(_v))
        return 0

    action = codex_integration.write_or_update(agents_md, version=_v)
    print(f"AGENTS.md: {action} burnless block at {agents_md}")
    return 0


def _worker_overrides_from_args(args) -> dict:
    """Collect per-call tier worker overrides from --diamond/--gold/--silver/--bronze."""
    return {t: getattr(args, t) for t in ("diamond", "gold", "silver", "bronze") if getattr(args, t, None)}


def cmd_do(args: argparse.Namespace) -> int:
    """Atomic delegate + run in a single command. Equivalent to `burnless do "prompt"`."""
    if os.environ.get("BURNLESS_WORKER") == "1" and os.environ.get("BURNLESS_ALLOW_NESTED") != "1":
        lang = os.environ.get("BURNLESS_LANG", "en")
        msg_text = i18n_mod.msg("guard_nested_delegation", lang)
        print(msg_text, file=sys.stderr)
        return 7
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)

    # Per-call worker overrides (--diamond/--gold/--silver/--bronze PROVIDER:MODEL)
    # are applied IN MEMORY only — never written to .burnless/config.yaml.
    # (2026-07-02 audit finding #2: the old disk-patch-then-restore-in-finally
    # approach left the override permanently in the file on crash/SIGKILL,
    # and raced with any parallel `burnless do`/`run` reading the same file
    # while the patch was live.)
    _worker_overrides = _worker_overrides_from_args(args)
    _effective_cfg = None
    if _worker_overrides:
        _effective_cfg = config_mod.apply_worker_overrides(config_mod.load(p["config"]), _worker_overrides)

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
    # cfg_override makes the rendered delegation reflect the EFFECTIVE agent
    # (post-override), not the pre-override tier default (finding #10).
    rc = cmd_delegate(delegate_args, cfg_override=_effective_cfg)
    if rc != 0:
        return rc

    # Read the id directly from the args object cmd_delegate just populated —
    # state.last_delegation is shared and gets overwritten by parallel cmd_do.
    did = getattr(delegate_args, "_allocated_did", None)
    if not did:
        print("burnless: delegate did not produce a delegation ID", file=sys.stderr)
        return 1

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
        worker_overrides=_worker_overrides or None,
    )
    return cmd_run(run_args)


def cmd_route(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    if getattr(args, "explain", False):
        task_kind = getattr(args, "task_kind", None)
        impact = getattr(args, "impact", None)
        tools_required = getattr(args, "tools_required", None)
        reversibility = getattr(args, "reversibility", None)
        context = None
        if task_kind is not None or impact is not None or tools_required is not None or reversibility is not None:
            context = routing_mod.RouteContext(
                task_kind=task_kind or "implement",
                impact=impact or "internal",
                tools_required=True if tools_required is None else tools_required,
                reversibility=reversibility or "reversible",
            )
        try:
            decision = routing_mod.decide_route(
                args.text, getattr(args, "tier", None), cfg["routing"], context=context
            )
        except ValueError as exc:
            print(f"burnless route: {exc}", file=sys.stderr)
            return 1
        agent = cfg["agents"].get(decision.effective_tier, {})
        print(routing_mod.format_route_explain(decision, agent.get("name", ""), agent.get("command", "")))
        if context is not None and context.task_kind == "architect" and context.tools_required is False:
            print(
                f"   suggestion:     tools_required=False + task_kind=architect -> "
                f"consider `burnless ask --tier {decision.effective_tier} \"...\"` instead of "
                f"`do` (no file/shell access needed)"
            )
        return 0
    info = routing_mod.explain_route(args.text, cfg["routing"])
    agent = cfg["agents"][info["tier"]]
    print(f"tier:    {info['tier']}")
    print(f"agent:   {agent['name']}  ({agent['command']})")
    print(f"matched: {info['matched_keyword'] or '(default)'}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    prompt = args.text if args.text else sys.stdin.read()
    if not prompt or not prompt.strip():
        print("burnless ask: empty prompt", file=sys.stderr)
        return 1

    request_id = uuid.uuid4().hex
    route_reason = "explicit --tier flag (or default)"
    warn_list: list[str] = []

    prefix_file = getattr(args, "prefix_file", None)
    prefix_content = None
    if prefix_file:
        try:
            prefix_content = Path(prefix_file).read_text(encoding="utf-8")
        except OSError as exc:
            error_message = f"burnless ask: --prefix-file unreadable: {prefix_file} ({exc.__class__.__name__})"
            events_mod.append_event(root, "ask.failed", {
                "request_id": request_id,
                "error_kind": "config_error",
                "error_message": error_message,
            })
            print(error_message, file=sys.stderr)
            return 1

    provider = getattr(args, "provider", None) or coreconfig_resolver.resolve_agent(args.tier, cfg).provider

    request = AskRequest(
        prompt=prompt,
        tier=args.tier,
        provider=provider,
        model=getattr(args, "model", None),
        system=args.system,
        effort=getattr(args, "effort", None),
        output_format=args.output_format,
        timeout_s=args.timeout,
        explain=getattr(args, "explain", False),
        dry_run=getattr(args, "dry_run", False),
        max_input_tokens=getattr(args, "max_input_tokens", None),
        max_output_tokens=getattr(args, "max_output_tokens", None),
        max_total_tokens=getattr(args, "max_total_tokens", None),
        max_budget_usd=getattr(args, "max_budget_usd", None),
        budget_policy=getattr(args, "budget_policy", "soft"),
        prefix_file=prefix_file,
        cache_key=getattr(args, "cache_key", None),
        request_id=request_id,
    )

    adapter = providers_mod.get_adapter(provider)
    if adapter is None:
        error_message = f"unsupported provider {provider!r} — no adapter registered"
        events_mod.append_event(root, "ask.failed", {
            "request_id": request_id,
            "error_kind": "config_error",
            "error_message": error_message,
        })
        print(f"burnless ask: {error_message}", file=sys.stderr)
        return 1

    target = adapter.resolve(request, cfg, prefix_content=prefix_content)

    estimated_input_tokens = target.budget.estimated_input_tokens
    over_total = (
        request.max_total_tokens is not None
        and estimated_input_tokens is not None
        and estimated_input_tokens > request.max_total_tokens
    )
    over_input = (
        request.max_input_tokens is not None
        and estimated_input_tokens is not None
        and estimated_input_tokens > request.max_input_tokens
    )
    if over_total or over_input:
        limit_kind = "max_total_tokens" if over_total else "max_input_tokens"
        limit_value = request.max_total_tokens if over_total else request.max_input_tokens
        error_message = (
            f"estimated input tokens ({estimated_input_tokens}) exceed "
            f"--{limit_kind.replace('_', '-')} ({limit_value}) before any provider call"
        )
        events_mod.append_event(root, "ask.failed", {
            "request_id": request_id,
            "error_kind": "budget_exceeded_preflight",
            "error_message": error_message,
        })
        if args.output_format == "json":
            envelope = pure_ask_mod.build_ask_envelope(
                request_id=request_id,
                requested_tier=target.requested_tier,
                effective_tier=target.effective_tier,
                provider=target.provider,
                model=target.model,
                effort=target.effort,
                route_source="explicit",
                route_reason=route_reason,
                route_signals=(),
                returncode=1,
                stdout="",
                stderr=error_message,
                dry_run=request.dry_run,
                warnings=tuple(warn_list),
            )
            envelope["status"] = "error"
            envelope["error_kind"] = "budget_exceeded_preflight"
            envelope["error_message"] = error_message
            print(json.dumps(envelope, indent=2, ensure_ascii=False))
        else:
            print(f"burnless ask: {error_message}", file=sys.stderr)
        return 1

    if request.dry_run:
        explain_dict = pure_ask_mod.render_ask_explain(target, request, route_reason=route_reason)
        ok = events_mod.append_event(root, "ask.dry_run", {
            "request_id": request_id,
            "effective_tier": target.effective_tier,
            "provider": target.provider,
            "model": target.model,
        })
        if not ok:
            warn_list.append("telemetry_write_failed")
        if args.output_format == "json":
            print(json.dumps(explain_dict, indent=2, ensure_ascii=False))
        else:
            for key, value in explain_dict.items():
                print(f"{key}: {value}")
        return 0

    ok = events_mod.append_event(root, "ask.started", {
        "request_id": request_id,
        "requested_tier": target.requested_tier,
        "provider": target.provider,
        "model": target.model,
        "cache_key": request.cache_key,
    })
    if not ok:
        warn_list.append("telemetry_write_failed")
    ok = events_mod.append_event(root, "ask.routed", {
        "request_id": request_id,
        "effective_tier": target.effective_tier,
        "route_source": "explicit",
        "route_reason": route_reason,
    })
    if not ok:
        warn_list.append("telemetry_write_failed")

    try:
        result = adapter.invoke_text(request, target, prefix_content=prefix_content)
    except subprocess.TimeoutExpired:
        events_mod.append_event(root, "ask.failed", {
            "request_id": request_id,
            "error_kind": "timeout",
            "error_message": f"timed out after {args.timeout}s",
        })
        print(f"burnless ask: timed out after {args.timeout}s", file=sys.stderr)
        return 1

    usage = adapter.parse_usage(result, target)

    actual_total_tokens = usage.input_tokens + usage.output_tokens
    over_output = (
        request.max_output_tokens is not None
        and usage.output_tokens > request.max_output_tokens
    )
    over_total_actual = (
        request.max_total_tokens is not None
        and actual_total_tokens > request.max_total_tokens
    )
    if over_output or over_total_actual:
        warn_list.append("budget_overage")
        ok = events_mod.append_event(root, "ask.budget_warning", {
            "request_id": request_id,
            "max_output_tokens": request.max_output_tokens,
            "max_total_tokens": request.max_total_tokens,
            "actual_output_tokens": usage.output_tokens,
            "actual_total_tokens": actual_total_tokens,
        })
        if not ok:
            warn_list.append("telemetry_write_failed")

    envelope = pure_ask_mod.build_ask_envelope(
        request_id=request_id,
        requested_tier=target.requested_tier,
        effective_tier=target.effective_tier,
        provider=target.provider,
        model=target.model,
        effort=target.effort,
        route_source="explicit",
        route_reason=route_reason,
        route_signals=(),
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        timed_out=result.timed_out,
        signal=result.signal,
        duration_ms=result.duration_ms,
        usage=usage,
        cache_mode=target.cache_mode,
        prefix_hash=target.prefix_hash,
        cache_key=request.cache_key,
        dry_run=False,
        warnings=tuple(warn_list),
    )

    if request.explain:
        envelope["explain"] = pure_ask_mod.render_ask_explain(target, request, route_reason=route_reason)

    lifecycle_event = "ask.completed" if envelope["status"] == "ok" else "ask.failed"
    ok = events_mod.append_event(root, lifecycle_event, {
        "request_id": request_id,
        "status": envelope["status"],
        "error_kind": envelope["error_kind"],
    })
    if not ok:
        warn_list.append("telemetry_write_failed")
        envelope["warnings"] = list(warn_list)

    if args.output_format == "json":
        print(json.dumps(envelope, indent=2, ensure_ascii=False))
        return 0 if envelope["status"] == "ok" else 1

    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    print(result.stdout.strip())
    return 0


def _normalize_trust_audit_text(text: str) -> str:
    """Same normalization rule as recovery._extract_verified_claims: strip
    surrounding backticks + whitespace, collapse internal whitespace."""
    return " ".join((text or "").strip().strip("`").split())


def _trust_audit(root_path, new_session_id: str, transcript_path: str | None = None, first_n: int = 10) -> dict:
    """Read-only audit: measure whether a new session re-verified claims
    already recorded as verified in the handoff's Verificado ledger."""
    try:
        owner_loop_path = Path(root_path) / ".burnless" / "owner_loop.jsonl"
        event = None
        if owner_loop_path.exists():
            with owner_loop_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get("event") == "restore_served" and obj.get("new_session_id") == new_session_id:
                        event = obj
        if event is None:
            return {"status": "no_restore_event", "new_session_id": new_session_id}

        claims = list(event.get("verified_claims") or [])
        handoff_age = event.get("handoff_age")

        if transcript_path:
            t_path = Path(transcript_path)
        else:
            cwd_dashes = os.getcwd().replace("/", "-")
            t_path = Path.home() / ".claude" / "projects" / cwd_dashes / f"{new_session_id}.jsonl"

        if not t_path.exists():
            return {
                "status": "no_transcript",
                "new_session_id": new_session_id,
                "n_claims": len(claims),
                "claims": claims,
            }

        observed: list[str] = []
        with t_path.open("r", encoding="utf-8") as f:
            for line in f:
                if len(observed) >= first_n:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("type") != "assistant":
                    continue
                content = ((rec.get("message") or {}).get("content")) or []
                if not isinstance(content, list):
                    continue
                for item in content:
                    if len(observed) >= first_n:
                        break
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") != "tool_use" or item.get("name") != "Bash":
                        continue
                    command = (item.get("input") or {}).get("command")
                    if command:
                        observed.append(_normalize_trust_audit_text(command))

        matched: list[str] = []
        for claim in claims:
            norm_claim = _normalize_trust_audit_text(claim)
            for obs in observed:
                if norm_claim in obs or obs in norm_claim:
                    matched.append(claim)
                    break

        reverify_rate = (len(matched) / len(claims)) if claims else 0.0
        stale_blind_rate = 1.0 if (
            handoff_age is not None and handoff_age > 1800 and len(matched) == 0 and claims
        ) else 0.0

        return {
            "status": "ok",
            "new_session_id": new_session_id,
            "claims": claims,
            "n_claims": len(claims),
            "observed": observed,
            "matched": matched,
            "reverify_rate": round(reverify_rate, 3),
            "stale_blind_rate": stale_blind_rate,
            "handoff_age": handoff_age,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


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
            root_path = epochs_mod.resolve_root(cwd, workspace=workspace, transcript=transcript)
        else:
            _fr = paths_mod.find_root()
            root_path = (_fr.parent if _fr else None)
        if root_path is None and getattr(args, "epoch_cmd", None) not in ("resolve-root", "extract-exchange"):
            # resolve-root handles its own None (incl. --orphan-fallback), and
            # extract-exchange only reads the transcript (root never used) —
            # the early exit here would kill both before they ever run (the
            # hooks pass --cwd for envelope metadata, not for root resolution).
            _cwd_for_msg = cwd if cwd is not None else Path.cwd()
            print(
                f"burnless epoch: no burnless project at {_cwd_for_msg} (no .burnless/config.yaml up-tree)",
                file=_sys.stderr,
            )
            return 1
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
        try:
            from . import epochs_v2
            if epochs_v2._epochs_version(root_path) >= 3:
                lp = epochs_v2.apply_capture(root_path, chat_id, text)
                if getattr(args, "emit_chain", False):
                    print("> ordem: documento vivo (living-doc v2)\n")
                    print(epochs_v2.living_seed(root_path, chat_id))
                else:
                    print(lp.name)
                return 0
        except Exception as e:
            if os.environ.get("BURNLESS_EPOCH_V2"):
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
            # Newest-first: seed.md feeds the carry-forward, truncated at top on clear-resume.
            # Top = latest live checkpoint (read new -> old).
            print("> order: newest first (top = latest live checkpoint)\n")
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
        if cwd is None:
            cwd = os.getcwd()
        workspace = getattr(args, "workspace", None)
        transcript = getattr(args, "transcript", None)
        r = epochs_mod.resolve_root(cwd, workspace=workspace, transcript=transcript)
        if (
            r is None
            and getattr(args, "orphan_fallback", False)
            and str(cwd or "").strip()  # empty cwd must never mint an orphan
            and not os.environ.get("BURNLESS_NO_ORPHAN")
        ):
            # Rolling-memory must survive /clear in ANY directory: fall back to
            # the deterministic per-cwd orphan store under ~/.burnless/orphans.
            # Same resolver on write (Stop/SessionEnd) and read (SessionStart)
            # -> write-root == read-root by construction. Promote later with
            # `burnless init` in this cwd.
            r = epochs_mod.ensure_orphan_root(cwd)
            if r is not None:
                print(f"[burnless] orphan rolling-memory for cwd={cwd} -> {r}", file=_sys.stderr)
        print(str(r) if r else "")
        return 0

    elif epoch_cmd == "handoff-path":
        # Canonical emitter: the EXACT live_handoff.md path the restore will
        # read for the resolved root. Writer-side instructions (clear-hint
        # hook, docs) must source the path from here, never rebuild it — this
        # keeps write-location == read-location by construction.
        print(str(recovery_mod.live_handoff_path_for(root_path)))
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

    elif epoch_cmd == "extract-exchange":
        transcript = getattr(args, "transcript", None)
        if not transcript:
            transcript = transcript_sources_mod.resolve_path(
                host=getattr(args, "host", "claude") or "claude",
                sid=getattr(args, "host_session_id", None) or getattr(args, "session_id", None) or "",
                cwd=getattr(args, "cwd", None),
            )
        if not transcript:
            print("")
            return 0
        envelope = recovery_mod.extract_exchange(
            transcript,
            host=getattr(args, "host", "claude"),
            host_session_id=getattr(args, "host_session_id", "") or getattr(args, "session_id", ""),
            process_instance_id=getattr(args, "process_instance_id", "") or getattr(args, "session_id", ""),
            cwd=getattr(args, "cwd", None),
            source=getattr(args, "source", None),
        )
        if envelope.get("transcript_found") is False:
            print(f"[warn] burnless epoch: transcript not found: {transcript}", file=_sys.stderr)
        print(json.dumps(envelope, ensure_ascii=False))
        return 0

    elif epoch_cmd == "journal-append":
        raw = _sys.stdin.read()
        if not raw.strip():
            print("")
            return 0
        envelope = json.loads(raw)
        record = recovery_mod.journal_append(getattr(args, "root", None) or root_path, envelope)
        print(json.dumps(record, ensure_ascii=False))
        return 0

    elif epoch_cmd == "compact-pending":
        root = getattr(args, "root", None) or root_path
        host = getattr(args, "host", "claude")
        host_session_id = getattr(args, "host_session_id", "") or getattr(args, "session_id", "")
        process_instance_id = getattr(args, "process_instance_id", "") or host_session_id
        source = getattr(args, "source", None)
        if not host_session_id:
            print("")
            return 0
        try:
            rewriter = None
            if getattr(args, "use_default_rewriter", True):
                from . import epochs_v2
                rewriter = epochs_v2.living_rewriter(root)
            result = recovery_mod.compact_pending(
                root,
                host=host,
                host_session_id=host_session_id,
                process_instance_id=process_instance_id,
                rewriter=rewriter or (lambda _prompt: None),
                source=source,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except Exception as exc:
            print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
            return 1

    elif epoch_cmd == "export":
        from . import exporting

        root = getattr(args, "root", None) or root_path
        host = getattr(args, "host", "claude")
        host_session_id = getattr(args, "host_session_id", "") or getattr(args, "session_id", "")
        if not host_session_id:
            print("")
            return 0
        result = exporting.export_epoch(root, host=host, host_session_id=host_session_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    elif epoch_cmd == "index":
        from . import exporting

        root = getattr(args, "root", None) or root_path
        result = exporting.backfill_epoch_index(root)
        added = result.get("added", 0)
        total = result.get("total", 0)
        print(f"epoch index: added {added} (total {total}) → {result.get('index_path', '')}")
        return 0

    elif epoch_cmd == "handoff-write":
        root = getattr(args, "root", None) or root_path
        host = getattr(args, "host", "claude")
        host_session_id = getattr(args, "host_session_id", "") or getattr(args, "session_id", "")
        process_instance_id = getattr(args, "process_instance_id", "") or host_session_id
        if not host_session_id:
            return 0
        payload = recovery_mod.write_handoff(
            root,
            host=host,
            host_session_id=host_session_id,
            process_instance_id=process_instance_id,
            claimed_by=getattr(args, "claimed_by", None),
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    elif epoch_cmd == "handoff-claim":
        root = getattr(args, "root", None) or root_path
        host = getattr(args, "host", "claude")
        process_instance_id = getattr(args, "process_instance_id", "") or getattr(args, "session_id", "")
        new_session_id = getattr(args, "new_session_id", "") or getattr(args, "session_id", "")
        if not process_instance_id or not new_session_id:
            print("")
            return 0
        payload = recovery_mod.claim_handoff(
            root,
            host=host,
            process_instance_id=process_instance_id,
            new_session_id=new_session_id,
            cwd=getattr(args, "cwd", None),
        )
        if payload is None:
            print("")
            return 0
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    elif epoch_cmd == "restore":
        root = getattr(args, "root", None) or root_path
        host = getattr(args, "host", "claude")
        host_session_id = getattr(args, "host_session_id", "") or getattr(args, "session_id", "")
        process_instance_id = getattr(args, "process_instance_id", "") or host_session_id
        new_session_id = getattr(args, "new_session_id", "") or getattr(args, "session_id", "")
        source = getattr(args, "source", "clear")
        if source == "clear" and os.environ.get("BURNLESS_PILOT_FORK") == "1":
            # Fork mode: canal C (--append-system-prompt) owns the restore;
            # the native SessionStart restore stays silent to avoid duplicating it.
            print("")
            return 0
        if source == "clear" and (not host_session_id or host_session_id == new_session_id):
            claimed = recovery_mod.claim_handoff(
                root,
                host=host,
                process_instance_id=process_instance_id,
                new_session_id=new_session_id,
                cwd=getattr(args, "cwd", None),
            )
            if claimed is None:
                # fresh_inherit: claim_handoff already called inherit_checkpoint
                # internally (never leaves the new window empty). Continue normal
                # flow with the newly inherited checkpoint.
                host_session_id = new_session_id
            else:
                host_session_id = str(claimed.get("host_session_id") or host_session_id)
                process_instance_id = str(claimed.get("process_instance_id") or process_instance_id)
        if not host_session_id:
            if source == "startup" and new_session_id:
                # Startup restore: no predecessor sid is known — render_restore
                # falls back to the latest project checkpoint for this host.
                host_session_id = new_session_id
            else:
                print("")
                return 0
        payload = recovery_mod.render_restore(
            root,
            host=host,
            host_session_id=host_session_id,
            process_instance_id=process_instance_id,
            new_session_id=new_session_id,
            source=source,
            budget_tokens=(
                int(getattr(args, "budget_tokens", None))
                if getattr(args, "budget_tokens", None) is not None
                else None  # A2: resolve from epochs.*_budget_tokens config
            ),
            transcript_path=getattr(args, "transcript", None),
        )
        if payload is None:
            print("")
            return 0
        try:
            # Eternal memory: bootstrap the new session's checkpoint from the
            # one just served, so compaction evolves the living doc across
            # rollovers instead of restarting it. Idempotent; never blocks
            # the restore output.
            served_old = str((payload.get("recovery") or {}).get("old_session") or host_session_id)
            recovery_mod.inherit_checkpoint(
                root,
                host=host,
                new_session_id=new_session_id,
                process_instance_id=process_instance_id,
                old_session_id=served_old,
            )
        except Exception:
            pass
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    elif epoch_cmd == "trust-audit":
        import json as _json
        result = _trust_audit(root_path, getattr(args, "sid", None), transcript_path=getattr(args, "transcript", None))
        print(_json.dumps(result, ensure_ascii=False))
        return 0

    elif epoch_cmd == "inherit":
        root = getattr(args, "root", None) or root_path
        host = getattr(args, "host", "claude")
        new_session_id = getattr(args, "new_session_id", "") or getattr(args, "session_id", "")
        process_instance_id = getattr(args, "process_instance_id", "") or new_session_id
        old_session_id = getattr(args, "host_session_id", None)
        if not new_session_id:
            print("")
            return 0
        committed = recovery_mod.inherit_checkpoint(
            root,
            host=host,
            new_session_id=new_session_id,
            process_instance_id=process_instance_id,
            old_session_id=old_session_id,
        )
        print(json.dumps({"status": "inherited" if committed else "noop"}, ensure_ascii=False))
        return 0

    elif epoch_cmd == "migrate-chains":
        root = getattr(args, "root", None) or root_path
        host = getattr(args, "host", "claude")
        result = recovery_mod.migrate_legacy_handoff_pool(root, host=host)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    elif epoch_cmd == "gc-chains":
        # kept as a compatible alias; `burnless gc [--dry-run]` is the simpler equivalent.
        root = getattr(args, "root", None) or root_path
        host = getattr(args, "host", "claude")
        result = recovery_mod.gc_dead_chains(root, host=host)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    elif epoch_cmd == "hook-error":
        root = getattr(args, "root", None) or root_path
        message = getattr(args, "message", None)
        if not message:
            message = _sys.stdin.read().strip()
        if not message:
            return 0
        recovery_mod.record_hook_error(
            root,
            hook=str(getattr(args, "hook", "unknown") or "unknown"),
            host=str(getattr(args, "host", "claude") or "claude"),
            host_session_id=getattr(args, "host_session_id", None) or getattr(args, "session_id", None),
            process_instance_id=getattr(args, "process_instance_id", None) or getattr(args, "session_id", None),
            source=getattr(args, "source", None),
            transcript_path=getattr(args, "transcript", None),
            error=message,
        )
        return 0

    elif epoch_cmd == "refine-owner":
        try:
            from datetime import datetime, timezone
            from . import epochs_v2, owner_loop, owner_cache

            result = epochs_mod.build_refine_owner_candidates(root_path, getattr(args, "chat_id", None))
            if result is None:
                return 0

            predecessors, floor_md = result

            try:
                cfg = config_mod.load(paths_mod.paths_for(root_path / ".burnless")["config"])
                enc = cfg.get("encoder") or {}
                owner_model = (enc.get("model") or "").strip()
            except Exception:
                owner_model = ""

            rewriter = epochs_v2.living_rewriter(root_path)
            if rewriter is None:
                return 0

            cache_path = str(root_path / ".burnless" / "epochs" / "_rolling" / "refined_seed.json")
            generated_at = datetime.now(timezone.utc).isoformat()

            owner_loop.refine_seed(
                cache_path=cache_path,
                predecessors=predecessors,
                floor_md=floor_md,
                rewriter=rewriter,
                owner_model=owner_model,
                generated_at=generated_at,
                exchange="",
                prompt_version="v3",
                root=root_path,
            )

            return 0
        except Exception:
            return 0

    return 2


def cmd_chat(args: argparse.Namespace) -> int:
    return chat_mod.main(args)











def cmd_economy(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    p = paths_mod.paths_for(root)
    cfg = config_mod.load(p["config"])
    from . import economy
    snap = _ledger_snapshot(p)
    r = economy.compute_economy_snapshot(snap, cfg)
    if getattr(args, "json", False):
        buckets_dicts = [
            {"name": b.name, "tokens": b.tokens, "usd": b.usd, "note": b.note}
            for b in r.buckets
        ]
        output = {
            "buckets": buckets_dicts,
            "total_tokens": r.total_tokens,
            "total_usd": r.total_usd,
            "accounted_total": r.accounted_total,
            "monetizable_subtotal": r.monetizable_subtotal,
            "excluded_categories": r.excluded_categories,
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
    from .pilot import summarize_session_log as pilot_summarize_session_log
    root = paths_mod.find_root()
    if root is None:
        print("burnless: not initialized in this directory tree. run `burnless init` first.", file=sys.stderr)
        return 1
    project_root = root.parent if root.name == ".burnless" else root

    mode = "rolling" if epochs_mod.is_enabled(root) else "default"

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

    recovery_summary = pilot_summarize_session_log(project_root)

    state = {
        "project": str(root.parent),
        "mode": mode,
        "last_status": last_status,
        "savings": savings,
        "scope_hash": scope_hash,
        "turns": turns,
        "checkpoint_generation": recovery_summary.get("checkpoint_generation"),
        "applied_through": recovery_summary.get("applied_through"),
        "journal_head": recovery_summary.get("journal_head"),
        "watermark_gap": recovery_summary.get("watermark_gap"),
        "pending_count": recovery_summary.get("pending_count"),
        "last_error": recovery_summary.get("last_error"),
        "claim_mode": recovery_summary.get("claim_mode"),
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
        active_mode = "rolling" if epochs_mod.is_enabled(root) else "default"

    sections = {
        "active_mode": active_mode,
        "last_hook_injection": latest("hook_injected"),
        "last_compaction_decision": latest("compaction_decision"),
        "last_route_decision": latest("route_decision"),
        "last_retrieval": latest("retrieve_called"),
        "last_delegation_status": latest("delegation_completed"),
        "last_warm_status": latest("warm_session_status"),
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
    sp.add_argument("--all", action="store_true", dest="show_all_chains", help="include stale/dead/unknown/archived chains, not just active")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("gc", help="archive dead/stale chains past the GC TTL (reuses gc_dead_chains)")
    sp.add_argument("--dry-run", action="store_true", dest="dry_run", help="report what would be archived without writing anything")
    sp.add_argument("--host", default="claude", help="host filter (default: claude)")
    sp.set_defaults(func=cmd_gc)

    sp = sub.add_parser("chat", help="view a chain as one continuous chat timeline")
    sp.add_argument("--host", default="claude", help="host filter (default: claude)")
    sp.add_argument("--chain", metavar="ID", help="chain id (default: newest live chain)")
    sp.add_argument("--list", action="store_true", help="list live chains for this project")
    sp.add_argument("--follow", action="store_true", help="follow the current transcript")
    sp.add_argument("--json", action="store_true", help="emit turn events as JSONL")
    sp.add_argument("--verbose", action="store_true", help="show tool names in rendered turns")
    sp.add_argument("--serve", nargs="?", const=8760, type=int, metavar="PORT", help="serve live HTML viewer (default port: 8760)")
    sp.set_defaults(func=cmd_chat)

    sp = sub.add_parser("metrics", help="show counters and estimated cost avoided")
    metrics_sub = sp.add_subparsers(dest="metrics_cmd")
    dsp = metrics_sub.add_parser("desktop", help="show desktop turn metrics from ~/.burnless/desktop/turns.jsonl")
    dsp.set_defaults(func=cmd_metrics_desktop)
    msp = metrics_sub.add_parser("migrate", help="one-time idempotent backfill: freeze metrics.json, re-emit spend.jsonl as usage_event/v1 kind=spend, emit legacy_snapshot")
    msp.set_defaults(func=cmd_metrics_migrate)
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
    sp.add_argument(
        "--explain",
        action="store_true",
        help="show line-by-line metrics provenance from audit.jsonl and spend.jsonl",
    )
    sp.add_argument(
        "--limit",
        type=int,
        default=50,
        help="max rows to show for --explain",
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

    sp = sub.add_parser("retrieve", help="retrieve local evidence for a delegation/file/entity")
    sp.add_argument("id", nargs="?", default=None, help="delegation id (optional)")
    sp.add_argument("--query", default=None)
    sp.add_argument("--file", default=None)
    sp.add_argument("--entity", default=None)
    sp.add_argument("--json", action="store_true", dest="json")
    sp.add_argument("--full", action="store_true", dest="full")
    sp.set_defaults(func=cmd_retrieve)

    sp = sub.add_parser("search-capsules", help="search indexed capsules by text")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--json", action="store_true", dest="json")
    sp.set_defaults(func=cmd_search_capsules)

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
    sp.add_argument("--task-kind", choices=["read", "classify", "create", "implement", "architect", "audit"], default=None, dest="task_kind", help="structured task shape — combines with --explain to test a routing.policies tier floor")
    sp.add_argument("--impact", choices=["internal", "public", "client", "production", "irreversible"], default=None, dest="impact", help="blast radius of the task — combines with --explain to test a routing.policies tier floor")
    sp.add_argument("--tools-required", dest="tools_required", action="store_true", default=None, help="task needs file/shell tool access (default when any context flag is set)")
    sp.add_argument("--no-tools-required", dest="tools_required", action="store_false", help="task needs no file/shell tool access (e.g. pure reasoning/drafting)")
    sp.add_argument("--reversibility", choices=["reversible", "hard_to_reverse", "irreversible"], default=None, dest="reversibility", help="how hard it is to undo the task's effects")
    sp.set_defaults(func=cmd_route)

    sp = sub.add_parser("ask", help="pure LLM completion — no tools, no CLAUDE.md, no agency (text in, text out). NOT do/delegate/run.")
    sp.add_argument("text", nargs="?", default=None, help="prompt (reads stdin if omitted)")
    sp.add_argument("--tier", choices=["diamond", "gold", "silver", "bronze"], default="silver", help="which tier's model to use")
    sp.add_argument("--model", default=None, help="model string explicit (bypasses tier/config.yaml resolution, ex: claude-opus-4-8)")
    sp.add_argument("--provider", default=None, choices=["anthropic", "ollama", "ollama-local", "codex"], help="explicit provider — required to disambiguate an explicit --model across providers")
    sp.add_argument("--system", default=None, help="override the default pure-completion system prompt")
    sp.add_argument("--output-format", choices=["text", "json"], default="text")
    sp.add_argument("--timeout", type=int, default=120)
    sp.add_argument("--max-budget-usd", type=float, default=None, help="hard per-call spend ceiling in USD (forwarded to claude -p)")
    sp.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"), default=None, help="reasoning effort forwarded to the resolved model")
    sp.add_argument("--explain", action="store_true", help="show the resolved target (tier/provider/model/capabilities/budget/redacted command) alongside the result")
    sp.add_argument("--dry-run", action="store_true", dest="dry_run", help="resolve everything and show what WOULD run, without calling the provider or writing spend")
    sp.add_argument("--max-input-tokens", type=int, default=None, dest="max_input_tokens")
    sp.add_argument("--max-output-tokens", type=int, default=None, dest="max_output_tokens")
    sp.add_argument("--max-total-tokens", type=int, default=None, dest="max_total_tokens")
    sp.add_argument("--budget-policy", choices=["hard", "soft"], default="soft", dest="budget_policy", help="hard: block when a capability proves it can enforce the cap; soft: estimate + warn only")
    sp.add_argument("--prefix-file", default=None, dest="prefix_file", help="path to a stable, versioned prefix appended to the system prompt (cache-friendly, hash-only telemetry)")
    sp.add_argument("--cache-key", default=None, dest="cache_key", help="opaque label for correlating prefix-cache calls in telemetry (not used for validation)")
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("setup", help="detect CLIs/keys and write a sensible config")
    sp.add_argument("--project", help="project name (default: current dir name)")
    sp.add_argument("--yes", "-y", action="store_true", help="accept all defaults")
    sp.add_argument("--non-interactive", action="store_true", help="no prompts")
    sp.add_argument("--codex", action="store_true", dest="codex", help="install the managed Burnless block into ~/.codex/AGENTS.md")
    sp.add_argument("--dry-run", action="store_true", dest="dry_run", help="show what would change without writing")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("warm", help="manage the warm session pool (cache-hit prefix for workers)")
    sp.set_defaults(func=lambda args, parser=sp: parser.print_help() or 0)
    warm_sub = sp.add_subparsers(dest="warm_cmd")
    wsp = warm_sub.add_parser("init", help="create a warm session for this project and seed W0")
    wsp.add_argument("--model", default=None,
                     help="model for warm session; only valid with --provider claude or --provider codex "
                          "(default: claude-sonnet-4-6 / codex's own default). With --provider both, use "
                          "--claude-model/--codex-model instead — a single --model would apply the SAME "
                          "model id to both CLIs, which is almost never correct.")
    wsp.add_argument("--claude-model", default=None, help="model for the claude warm pool (with --provider both)")
    wsp.add_argument("--codex-model", default=None, help="model for the codex warm pool (with --provider both)")
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
    wsp = warm_sub.add_parser("explain", help="explain warm pool state, TTL, and compaction caution")
    wsp.add_argument("--provider", choices=["claude", "codex", "both"], default="both")
    wsp.add_argument("--json", action="store_true", dest="json")
    wsp.set_defaults(func=cmd_warm_explain)

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
        "pilot",
        help="spawn the host CLI through a transparent PTY relay (claude/codex)",
    )
    sp.add_argument("--doctor", action="store_true", help="show host probe and exit")
    sp.add_argument("--report", action="store_true", help="show resolved pilot config and host probe")
    sp.add_argument("--auto-rollover", action="store_true", help="enable event-driven rollover monitor")
    sp.add_argument("--no-auto", action="store_true", dest="no_auto", help="disable auto-rollover even when the host supports it (auto-rollover is ON by default)")
    sp.add_argument("--cadence", action="store_true", help="route C: continuous in-thread /compact driven by the CadenceController")
    sp.add_argument("--host", choices=["auto", "claude", "codex"], default="auto", help="host CLI to launch")
    sp.add_argument("--model", default=None, help="optional model override forwarded to the host")
    sp.add_argument("--run-id", default=None, dest="run_id", help="optional lineage id")
    sp.add_argument("--chrome", action="store_true", help="claude host capability: launch with Claude-in-Chrome enabled (fails loud on other hosts)")
    sp.add_argument("extra_args", nargs=argparse.REMAINDER, help="extra args forwarded verbatim (a leading -- separator is stripped, never forwarded as prompt)")
    sp.set_defaults(func=cmd_pilot, pilot_cmd="run")

    sp = sub.add_parser("pty", help="alias for pilot")
    sp.add_argument("--doctor", action="store_true", help="show host probe and exit")
    sp.add_argument("--report", action="store_true", help="show resolved pilot config and host probe")
    sp.add_argument("--auto-rollover", action="store_true", help="enable event-driven rollover monitor")
    sp.add_argument("--no-auto", action="store_true", dest="no_auto", help="disable auto-rollover even when the host supports it (auto-rollover is ON by default)")
    sp.add_argument("--cadence", action="store_true", help="route C: continuous in-thread /compact driven by the CadenceController")
    sp.add_argument("--host", choices=["auto", "claude", "codex"], default="auto", help="host CLI to launch")
    sp.add_argument("--model", default=None, help="optional model override forwarded to the host")
    sp.add_argument("--run-id", default=None, dest="run_id", help="optional lineage id")
    sp.add_argument("--chrome", action="store_true", help="claude host capability: launch with Claude-in-Chrome enabled (fails loud on other hosts)")
    sp.add_argument("extra_args", nargs=argparse.REMAINDER, help="extra args forwarded verbatim (a leading -- separator is stripped, never forwarded as prompt)")
    sp.set_defaults(func=cmd_pilot, pilot_cmd="run")

    sp = sub.add_parser("pilot-event", help=argparse.SUPPRESS)
    sp.add_argument("--root", default=None, help="project root (default: find_root())")
    sp.add_argument("--run-id", default=None, dest="run_id", help="pilot run id")
    sp.add_argument("--event", default=None, help="event name override")
    sp.add_argument("--host", default=None, help="host name")
    sp.add_argument("--host-session-id", default=None, dest="host_session_id", help="host session id")
    sp.add_argument("--process-instance-id", default=None, dest="process_instance_id", help="process instance id")
    sp.add_argument("--source", default=None, help="hook source")
    sp.add_argument("--cwd", default=None, help="working directory")
    sp.add_argument("--transcript", default=None, help="transcript path")
    sp.set_defaults(func=cmd_pilot_event)

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
    epoch_core = argparse.ArgumentParser(add_help=False)
    epoch_core.add_argument("--root", default=None, help="burnless root (.burnless)")
    epoch_core.add_argument("--transcript", default=None, help="transcript file path (extract-exchange)")
    epoch_core.add_argument("--cwd", default=None, help="working directory recorded in the envelope")
    epoch_core.add_argument("--host", default="claude", help="host name for recovery paths")
    epoch_core.add_argument("--host-session-id", default=None, dest="host_session_id", help="host session id")
    epoch_core.add_argument("--process-instance-id", default=None, dest="process_instance_id", help="stable process/window instance id")
    epoch_core.add_argument("--session-id", default=None, dest="session_id", help="compat alias for host/session ids")
    epoch_core.add_argument("--source", default=None, help="hook source (startup|clear|resume|...)")
    epoch_core.add_argument("--new-session-id", default=None, dest="new_session_id", help="new session id for clear handoff restore")
    epoch_core.add_argument("--budget-tokens", default=None, dest="budget_tokens", type=int,
                            help="restore budget in tokens (default: epochs.restore_budget_tokens / epochs.startup_budget_tokens from config)")
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
    esp.add_argument("--orphan-fallback", action="store_true", dest="orphan_fallback", default=False,
                     help="when no project resolves, fall back to the deterministic per-cwd orphan store under ~/.burnless/orphans (rolling memory survives /clear anywhere); disable with BURNLESS_NO_ORPHAN=1")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="resolve-root")
    esp = epoch_sub.add_parser("resume", parents=[epoch_common], help="emit carry-forward chain")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="resume")
    esp = epoch_sub.add_parser("extract-exchange", parents=[epoch_core], help="extract the last exchange from a transcript")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="extract-exchange")
    esp = epoch_sub.add_parser("journal-append", parents=[epoch_core], help="append a structured exchange record to the journal")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="journal-append")
    esp = epoch_sub.add_parser("compact-pending", parents=[epoch_core], help="compact journal entries into a checkpoint")
    esp.add_argument("--use-default-rewriter", action="store_true", dest="use_default_rewriter", default=True)
    esp.set_defaults(func=cmd_epoch, epoch_cmd="compact-pending")
    esp = epoch_sub.add_parser("export", parents=[epoch_core], help="export consolidated living_md as a neutral artifact under .burnless/exports/")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="export")
    esp = epoch_sub.add_parser("index", parents=[epoch_common], help="backfill per-project epoch INDEX.md from exports")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="index")
    esp = epoch_sub.add_parser("handoff-write", parents=[epoch_core], help="write a clear handoff record")
    esp.add_argument("--claimed-by", default=None, dest="claimed_by", help="pre-claim by session id, when applicable")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="handoff-write")
    esp = epoch_sub.add_parser("handoff-claim", parents=[epoch_core], help="claim the freshest clear handoff")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="handoff-claim")
    esp = epoch_sub.add_parser("handoff-path", parents=[epoch_core], help="print the canonical live_handoff.md path the restore will read (writer instructions must use this)")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="handoff-path")
    esp = epoch_sub.add_parser("restore", parents=[epoch_core], help="render checkpoint + pending delta for SessionStart")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="restore")
    esp = epoch_sub.add_parser("trust-audit", parents=[epoch_core], help="measure re-verification rate of a restored handoff's Verificado ledger against a new session transcript")
    esp.add_argument("--sid", dest="sid", required=True, help="new session id to audit")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="trust-audit")
    esp = epoch_sub.add_parser("inherit", parents=[epoch_core], help="bootstrap the new session checkpoint from its predecessor (memoria eterna)")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="inherit")
    esp = epoch_sub.add_parser("hook-error", parents=[epoch_core], help="append a hook error to the global ledger")
    esp.add_argument("--hook", default="unknown", help="hook name (Stop/SessionStart/SessionEnd)")
    esp.add_argument("--message", default=None, help="error message (defaults to stdin)")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="hook-error")
    esp = epoch_sub.add_parser("migrate-chains", parents=[epoch_common], help="migrate legacy handoff pool entries into per-chain storage (idempotent)")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="migrate-chains")
    esp = epoch_sub.add_parser("gc-chains", parents=[epoch_common], help="archive dead chains older than 7 days, exporting their consolidated living_md first")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="gc-chains")
    esp = epoch_sub.add_parser("refine-owner", parents=[epoch_common], help="async refine owner-loop seed from V2 predecessors")
    esp.set_defaults(func=cmd_epoch, epoch_cmd="refine-owner")

    sp = sub.add_parser("doctor", help="healthcheck install/config/wiring/MCP; --fix auto-remediates safe issues")
    sp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sp.add_argument("--fix", action="store_true",
                    help="auto-remediate safe issues (write config, wire hooks, copy managed files, register MCP) then re-check")
    sp.add_argument("--prefix-file", default=None, dest="prefix_file", help="scan this ask --prefix-file path for secret-shaped content (soft warning, not a hard failure)")
    sp.add_argument("--codex", action="store_true", help="also run the Codex integration check group (binary, AGENTS.md, hooks, provider config) — informational only")
    sp.set_defaults(func=cmd_doctor)

    return p


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import doctor as doctor_mod
    checks = doctor_mod.run_checks(
        fix=bool(getattr(args, "fix", False)),
        prefix_file=getattr(args, "prefix_file", None),
        codex=bool(getattr(args, "codex", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(doctor_mod.render_json(checks), indent=2, ensure_ascii=False))
    else:
        print(doctor_mod.render_human(checks))
    return doctor_mod.exit_code(checks)


def _pilot_normalize_extra_args(raw_extra, chrome: bool, host_name: str):
    """P11.1: argparse.REMAINDER keeps the literal `--` separator, and the host
    CLI then treats everything after it as PROMPT text (the `--chrome`-as-prompt
    footgun, Diario 21/07). Strip the separator, and materialize --chrome as an
    explicit claude-host capability. Returns (extra_args, error_message)."""
    extra = list(raw_extra or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    if chrome:
        if host_name != "claude":
            return None, (
                f"--chrome e uma capability do host claude; host resolvido: {host_name}. "
                "Use --host claude ou remova --chrome."
            )
        if "--chrome" not in extra:
            extra = extra + ["--chrome"]
    return extra, None


def cmd_pilot_event(args: argparse.Namespace) -> int:
    from .pilot import append_event
    from .pilot.core import PilotEvent

    raw = sys.stdin.read()
    payload: dict[str, object] = {}
    if raw.strip():
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                payload = obj
        except Exception:
            payload = {}

    root = getattr(args, "root", None)
    if root is None:
        root = paths_mod.find_root()
        if root is None:
            return 0
    root = Path(root)
    if root.name == ".burnless":
        root = root.parent
    run_id = getattr(args, "run_id", None) or os.environ.get("BURNLESS_PILOT_RUN_ID")
    if not run_id:
        return 0

    event_name = str(getattr(args, "event", None) or payload.get("event") or payload.get("hookEventName") or "unknown")
    host = str(getattr(args, "host", None) or payload.get("host") or "unknown")
    host_session_id = getattr(args, "host_session_id", None) or payload.get("session_id") or payload.get("host_session_id")
    process_instance_id = getattr(args, "process_instance_id", None) or payload.get("process_instance_id")
    source = str(getattr(args, "source", None) or payload.get("source") or payload.get("reason") or "")
    cwd = str(getattr(args, "cwd", None) or payload.get("cwd") or "")
    transcript_ref = str(getattr(args, "transcript", None) or payload.get("transcript_path") or payload.get("transcript_ref") or "")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
    event = PilotEvent(
        host=host,
        host_session_id=str(host_session_id) if host_session_id else None,
        process_instance_id=str(process_instance_id) if process_instance_id else None,
        event=event_name,
        source=source or None,
        cwd=cwd or None,
        transcript_ref=transcript_ref or None,
        user_text=payload.get("user_text") if isinstance(payload.get("user_text"), str) else None,
        assistant_text=payload.get("assistant_text") if isinstance(payload.get("assistant_text"), str) else None,
        usage=usage,
        ts=str(payload.get("ts") or payload.get("timestamp") or datetime.now(timezone.utc).isoformat()),
    )
    append_event(root, str(run_id), event)
    return 0


def _pilot_next_session_id(run_id: str, rollover_index: int) -> str:
    if rollover_index <= 1:
        return f"{run_id}-fresh"
    return f"{run_id}-fresh-{rollover_index}"


def _extract_rollover_meta(rollover_result):
    last = (rollover_result or {}).get("last") or {}
    prepared = last.get("prepared") or {}
    return last, prepared, last.get("new_session_id")


def _pilot_rollover_circuit_open(rollover_ts_list, now_ts, *, max_rollovers: int = 3, window_s: float = 30.0) -> bool:
    recent = [ts for ts in rollover_ts_list if now_ts - ts <= window_s]
    return len(recent) >= max_rollovers


def _pilot_yesno(value: object) -> str:
    return "yes" if bool(value) else "no"


def _pilot_available_hosts() -> list[str]:
    return [inst.name for inst in pilot_discover_hosts() if inst.available]


def _pilot_normalize_host_choice(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return None if not value or value == "auto" else value


def _pilot_prompt_host_choice(choices: list[str]) -> str:
    labels = ", ".join(f"{idx + 1}) {name}" for idx, name in enumerate(choices))
    while True:
        raw = input(f"burnless pilot: choose host [{labels}]: ").strip()
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        normalized = raw.lower()
        for name in choices:
            if normalized == name:
                return name
        print(f"burnless pilot: invalid choice {raw!r}; pick one of {labels}", file=sys.stderr)


def _pilot_select_host_choice(root: Path, requested_host: str | None) -> str:
    if requested_host and requested_host != "auto":
        return requested_host
    available = _pilot_available_hosts()
    if len(available) <= 1:
        return available[0] if available else "auto"
    if not sys.stdin.isatty():
        chosen = available[0]
        print(
            "burnless pilot: non-interactive launch detected; "
            f"defaulting host -> {chosen}. "
            "Use --host or set pilot.host to skip the prompt.",
            file=sys.stderr,
        )
        return chosen
    chosen = _pilot_prompt_host_choice(available)
    try:
        cfg = config_mod.load(root / "config.yaml")
        if isinstance(cfg, dict):
            pilot_cfg = cfg.setdefault("pilot", {})
            pilot_cfg["host"] = chosen
            config_mod.save(root / "config.yaml", cfg)
    except Exception:
        pass
    print(f"burnless pilot: host selected -> {chosen}")
    return chosen


def _run_pilot_cycle(
    *,
    project_root: Path,
    adapter,
    model: str | None,
    extra_args: list[str],
    run_id: str,
    host_session_id: str,
    pilot_cfg: dict,
    enable_monitor: bool,
    is_restart_cycle: bool,
    fresh_session_id: str | None,
    host_arg: str,
    initial_input_bytes: bytes | None = None,
    cadence_enabled: bool = False,
    cadence_cfg: dict | None = None,
    fork: bool = False,
) -> dict:
    argv = adapter.build_fresh_argv(project_root, model=model, extra_args=extra_args) if is_restart_cycle else adapter.build_interactive_argv(project_root, model=model, extra_args=extra_args)
    from .pilot.core import build_child_env
    env = build_child_env(run_id, fork=fork)

    installation = adapter.detect() if hasattr(adapter, "detect") else type("I", (), {"version": None})()
    caps = adapter.capabilities() if hasattr(adapter, "capabilities") else type("C", (), {"reset_strategy": "respawn"})()
    session_probe = adapter.locate_session(host_session_id) if hasattr(adapter, "locate_session") else None
    if session_probe is not None and getattr(session_probe, "cwd", None) is None:
        try:
            session_probe = replace(session_probe, cwd=str(project_root))
        except Exception:
            pass
    def _usage_for_host_session() -> object:
        if not (hasattr(adapter, "locate_session") and hasattr(adapter, "context_usage")):
            return type("U", (), {"current": None, "limit": None, "confidence": "unknown"})()
        try:
            session = adapter.locate_session(run_id)
        except Exception:
            return type("U", (), {"current": None, "limit": None, "confidence": "unknown"})()
        if session is None:
            return type("U", (), {"current": None, "limit": None, "confidence": "unknown"})()
        if getattr(session, "cwd", None) is None:
            try:
                session = replace(session, cwd=str(project_root))
            except Exception:
                pass
        try:
            return adapter.context_usage(session)
        except Exception:
            return type("U", (), {"current": None, "limit": None, "confidence": "unknown"})()

    context = _usage_for_host_session()
    context_before = {
        "current": getattr(context, "current", None),
        "limit": getattr(context, "limit", None),
        "confidence": getattr(context, "confidence", "unknown"),
    }

    start = time.time()
    stop_event = None
    monitor_thread = None
    rollover_state: dict = {}

    def _start_monitor(proc) -> None:
        nonlocal stop_event, monitor_thread
        if not enable_monitor:
            return
        stop_event = threading.Event()
        spawn_ts = datetime.now(timezone.utc).isoformat()

        def _monitor() -> None:
            try:
                result = pilot_monitor_rollover_loop(
                    project_root,
                    host=getattr(adapter, "name", host_arg),
                    host_session_id=host_session_id,
                    process_instance_id=host_session_id,
                    run_id=run_id,
                    new_session_id=fresh_session_id or _pilot_next_session_id(run_id, 1),
                    context_usage_fn=_usage_for_host_session,
                    rollover_at_tokens=int(pilot_cfg.get("rollover_at_tokens", 40000)),
                    rollover_at_pct=float(pilot_cfg.get("rollover_at_pct", 0.65)),
                    delta_budget_tokens=int(pilot_cfg.get("delta_budget_tokens", 2000)),
                    poll_interval_s=float(pilot_cfg.get("poll_interval_s", 0.5)),
                    stop_event=stop_event,
                    trusted_confidences=("exact", "estimated") if bool(pilot_cfg.get("trust_estimated_usage", False)) else ("exact",),
                    since_ts=spawn_ts,
                )
                rollover_state["result"] = result
                last = result.get("last") or {}
                if last.get("status") in {"armed", "prepared"}:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except Exception:
                        pass
            except Exception as exc:
                rollover_state["error"] = str(exc)

        monitor_thread = threading.Thread(target=_monitor, daemon=True)
        monitor_thread.start()

    pilot_append_session_log(
        project_root,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "host": getattr(adapter, "name", host_arg),
            "host_version": getattr(installation, "version", None),
            "strategy": getattr(caps, "reset_strategy", "respawn"),
            "context_confidence": getattr(context, "confidence", "unknown"),
            "host_session_id": host_session_id,
            "old_session": host_session_id,
            "context_before": context_before,
            "checkpoint_chars": 0,
            "pending_count": 0,
            "turns": 0,
            "checkpoint_generation": 0,
            "watermark_gap": 0,
            "duration_ms": 0,
            "phase": "restart_start" if is_restart_cycle else "start",
        },
    )

    try:
        pilot_kwargs = {
            "cwd": str(project_root),
            "env": env,
            "on_spawn": _start_monitor if enable_monitor else None,
        }
        if initial_input_bytes is not None:
            pilot_kwargs["input_bytes"] = initial_input_bytes
        if str(pilot_cfg.get("hud", "title")) == "title":
            pilot_kwargs["title_provider"] = lambda: hud_mod.hud_title(project_root)
        if cadence_enabled:
            controller = build_cadence_controller(adapter=adapter, project_root=project_root, run_id=run_id, host_session_id=host_session_id, cfg=cadence_cfg or {})
            inner_injector = _build_cadence_injector(controller, time.monotonic)
            def wrapped_injector() -> bytes | None:
                result = inner_injector()
                if result is not None:
                    try:
                        pilot_append_event(
                            project_root,
                            run_id,
                            {"event": "compact_issued", "ts": datetime.now(timezone.utc).isoformat()}
                        )
                    except Exception:
                        pass
                return result
            pilot_kwargs["injector"] = wrapped_injector
        rc = pilot_run(argv, **pilot_kwargs)
    finally:
        if stop_event is not None:
            stop_event.set()
        if monitor_thread is not None:
            monitor_thread.join(timeout=1.0)

    rollover_result = rollover_state.get("result") or {}
    last, prepared, new_session_id = _extract_rollover_meta(rollover_result)
    restore_meta = (prepared.get("restore") or {}).get("recovery") or {}
    turns = int((prepared.get("run_state") or {}).get("count") or 0)
    pilot_append_session_log(
        project_root,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "host": getattr(adapter, "name", host_arg),
            "host_version": getattr(installation, "version", None),
            "strategy": getattr(caps, "reset_strategy", "respawn"),
            "context_confidence": getattr(context, "confidence", "unknown"),
            "host_session_id": host_session_id,
            "old_session": host_session_id,
            "context_before": context_before,
            "checkpoint_chars": int(restore_meta.get("checkpoint_chars") or 0),
            "pending_count": int(restore_meta.get("pending_count") or 0),
            "turns": turns,
            "checkpoint_generation": restore_meta.get("checkpoint_generation"),
            "journal_head": restore_meta.get("journal_head"),
            "applied_through": restore_meta.get("applied_through"),
            "watermark_gap": restore_meta.get("watermark_gap"),
            "claim_mode": restore_meta.get("claim_mode"),
            "last_error": rollover_state.get("error"),
            "truncated": restore_meta.get("truncated"),
            "duration_ms": int((time.time() - start) * 1000),
            "phase": "restart_end" if is_restart_cycle else "end",
            "returncode": int(rc if isinstance(rc, int) else rc[0]),
            "new_session": (
                new_session_id
                if last.get("status") == "prepared"
                else None
            ),
            "rollover": rollover_result,
        },
    )
    return {
        "rc": int(rc if isinstance(rc, int) else rc[0]),
        "rollover": rollover_result,
        "error": rollover_state.get("error"),
        "run_id": run_id,
        "host_session_id": host_session_id,
        "session_id": host_session_id,
        "argv": argv,
    }


def _pilot_resolve_auto_rollover(
    *,
    cli_auto_rollover: bool,
    cli_no_auto: bool,
    config_auto_rollover: object,
    adapter: object,
    host_name: str,
) -> tuple[bool, str | None]:
    """Resolve the effective auto-rollover flag.

    Auto-rollover is ON by default (product default) unless explicitly disabled via
    --no-auto or `pilot.auto_rollover: false` in config. It can only turn on when the
    host declares full capability (supports_hooks AND supports_usage); otherwise it
    stays off and a non-empty diagnostic string is returned explaining why.
    """
    requested = bool(cli_auto_rollover) or (
        True if config_auto_rollover is None else bool(config_auto_rollover)
    )
    if cli_no_auto or not requested:
        return False, None
    if not hasattr(adapter, "capabilities"):
        return True, None
    capabilities = adapter.capabilities()
    capable = bool(getattr(capabilities, "supports_hooks", False)) and bool(
        getattr(capabilities, "supports_usage", False)
    )
    if not capable:
        diagnostic = (
            f"[pilot] auto-rollover desarmado: host '{host_name}' sem capabilities completas "
            f"(supports_hooks={bool(getattr(capabilities, 'supports_hooks', False))}, "
            f"supports_usage={bool(getattr(capabilities, 'supports_usage', False))})"
        )
        return False, diagnostic
    return True, None


def _pilot_fork_enabled(project_root: Path) -> bool:
    """Read pilot.fork.enabled from the project config. Default False."""
    try:
        cfg = config_mod.load(project_root / ".burnless" / "config.yaml")
    except Exception:
        return False
    if not isinstance(cfg, dict):
        return False
    pilot_cfg = cfg.get("pilot", {})
    if not isinstance(pilot_cfg, dict):
        return False
    fork_cfg = pilot_cfg.get("fork", {})
    if not isinstance(fork_cfg, dict):
        return False
    return bool(fork_cfg.get("enabled", False))


def cmd_pilot(args: argparse.Namespace) -> int:
    root = paths_mod.require_root()
    project_root = root.parent if root.name == ".burnless" else root
    cfg = config_mod.load(root / "config.yaml")
    pilot_cfg = cfg.get("pilot", {}) if isinstance(cfg, dict) else {}

    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    if getattr(args, "doctor", False) or getattr(args, "report", False):
        hosts = pilot_discover_hosts()
        print("Burnless pilot host probe")
        print(f"  project_root: {project_root}")
        print(f"  configured host: {pilot_cfg.get('host', 'auto')}")
        print(f"  configured model: {pilot_cfg.get('model', '(default)')}")
        print(f"  rollover mode: {pilot_cfg.get('rollover_mode', 'respawn')}")
        print(f"  auto_rollover: {pilot_cfg.get('auto_rollover', 'default-on (use --no-auto to disable)')}")
        print(f"  trust_estimated_usage: {pilot_cfg.get('trust_estimated_usage', False)}")
        print(f"  rollover_at_tokens: {pilot_cfg.get('rollover_at_tokens', 40000)}")
        print(f"  rollover_at_pct: {pilot_cfg.get('rollover_at_pct', 0.65)}")
        print(f"  delta_budget_tokens: {pilot_cfg.get('delta_budget_tokens', 2000)}")
        print(f"  hud: {pilot_cfg.get('hud', 'title')}")
        for inst in hosts:
            status = "available" if inst.available else "missing"
            version = inst.version or "(unknown)"
            path = inst.path or "(not found)"
            print(f"  {inst.name:<6} {status:<10} {version}  {path}")
            adapter = pilot_resolve_host_adapter(inst.name, root=project_root, env_host=os.environ.get("BURNLESS_PILOT_HOST"))
            caps = adapter.capabilities() if hasattr(adapter, "capabilities") else type("C", (), {})()
            print(
                "    caps:"
                f" hooks={_pilot_yesno(getattr(caps, 'supports_hooks', False))}"
                f" usage={_pilot_yesno(getattr(caps, 'supports_usage', False))}"
                f" trust={getattr(caps, 'trust', 'unknown')}"
                f" transcript={_pilot_yesno(getattr(caps, 'transcript_access', False))}"
                f" rollout={_pilot_yesno(getattr(caps, 'rollout_access', False))}"
                f" reset={getattr(caps, 'reset_strategy', 'respawn')}"
            )
        report = pilot_build_report(
            getattr(args, "host", None),
            root=project_root,
            env_host=os.environ.get("BURNLESS_PILOT_HOST"),
            run_id=getattr(args, "run_id", None),
        )
        caps = _get(report, "capabilities", None)
        usage = _get(report, "usage", None)
        print(f"  strategy: {_get(caps, 'reset_strategy', 'respawn')}")
        print(f"  context: {_get(usage, 'confidence', 'unknown')} {_get(usage, 'current', '-') or '-'} / {_get(usage, 'limit', '-') or '-'}")
        run_state = _get(report, "run_state", None)
        if run_state:
            print(f"  run_state: {_get(run_state, 'state', 'unknown')} (last={_get(run_state, 'last_event', '-')})")
        summary = pilot_summarize_session_log(project_root)
        if summary["count"]:
            print(f"  sessions logged: {summary['count']}")
            print(f"  last session: {summary['host']} {summary['host_session_id']} -> {summary['new_session_id']}")
            if summary.get("host_version"):
                print(f"  last host version: {summary['host_version']}")
            context_before = summary.get("context_before")
            if isinstance(context_before, dict):
                print(
                    "  context before: "
                    f"{context_before.get('confidence', 'unknown')} "
                    f"{context_before.get('current', '-') or '-'} / {context_before.get('limit', '-') or '-'}"
                )
            if summary.get("checkpoint_chars") is not None:
                print(
                    f"  checkpoint_chars: {summary.get('checkpoint_chars')}  "
                    f"pending_count: {summary.get('pending_count')}  "
                    f"turns: {summary.get('turns')}"
                )
            if summary.get("checkpoint_generation") is not None or summary.get("journal_head") is not None:
                print(
                    f"  recovery: gen={summary.get('checkpoint_generation', '-')}"
                    f" applied={summary.get('applied_through', '-')}"
                    f" head={summary.get('journal_head', '-')}"
                    f" gap={summary.get('watermark_gap', '-')}"
                )
            if summary.get("claim_mode"):
                print(f"  claim_mode: {summary.get('claim_mode')}")
            if summary.get("last_error"):
                print(f"  last_error: {summary.get('last_error')}")
        return 0

    requested_host = (
        _pilot_normalize_host_choice(getattr(args, "host", None))
        or _pilot_normalize_host_choice(os.environ.get("BURNLESS_PILOT_HOST"))
        or _pilot_normalize_host_choice(pilot_cfg.get("host"))
    )
    selected_host = _pilot_select_host_choice(root, requested_host)
    adapter = pilot_resolve_host_adapter(
        selected_host,
        root=project_root,
        env_host=os.environ.get("BURNLESS_PILOT_HOST"),
    )
    model = getattr(args, "model", None)
    if model is None:
        model = pilot_cfg.get("model")
    _extra_raw = getattr(args, "extra_args", []) or list(pilot_cfg.get("extra_args") or [])
    extra_args, _extra_err = _pilot_normalize_extra_args(
        _extra_raw, bool(getattr(args, "chrome", False)), selected_host
    )
    if _extra_err:
        print(f"burnless pilot: {_extra_err}", file=sys.stderr)
        return 2
    run_id = getattr(args, "run_id", None) or f"pilot-{int(time.time())}"
    auto_rollover, _auto_rollover_diagnostic = _pilot_resolve_auto_rollover(
        cli_auto_rollover=bool(getattr(args, "auto_rollover", False)),
        cli_no_auto=bool(getattr(args, "no_auto", False)),
        config_auto_rollover=pilot_cfg.get("auto_rollover"),
        adapter=adapter,
        host_name=selected_host,
    )
    if _auto_rollover_diagnostic:
        print(_auto_rollover_diagnostic, file=sys.stderr)
    cadence_cfg = pilot_cfg.get("cadence", {}) if isinstance(pilot_cfg, dict) else {}
    cadence_enabled = bool(getattr(args, "cadence", False)) or bool(cadence_cfg.get("enabled", False))
    if cadence_enabled and not bool(getattr(args, "auto_rollover", False)):
        auto_rollover = False
    current_session_id = run_id
    rollover_index = 1
    rc = 0
    pending_initial_input: bytes | None = None
    pending_extra_args: list[str] = []
    fork_enabled = _pilot_fork_enabled(project_root)
    rollover_ts_list: list[float] = []

    try:
        while True:
            next_session_id = _pilot_next_session_id(run_id, rollover_index)
            cycle = _run_pilot_cycle(
                project_root=project_root,
                adapter=adapter,
                model=model,
                extra_args=extra_args + pending_extra_args,
                run_id=run_id,
                host_session_id=current_session_id,
                pilot_cfg=pilot_cfg,
                enable_monitor=auto_rollover,
                is_restart_cycle=current_session_id != run_id,
                fresh_session_id=next_session_id if auto_rollover else None,
                host_arg=getattr(args, "host", "auto"),
                initial_input_bytes=pending_initial_input,
                cadence_enabled=cadence_enabled,
                cadence_cfg=cadence_cfg,
                fork=fork_enabled,
            )
            pending_initial_input = None
            pending_extra_args = []
            rc = cycle["rc"]
            if not auto_rollover:
                break

            rollover = cycle.get("rollover") or {}
            last, prepared, new_session_id_from_monitor = _extract_rollover_meta(rollover)
            if last.get("status") != "prepared":
                break

            now_ts = time.time()
            rollover_ts_list.append(now_ts)
            if _pilot_rollover_circuit_open(rollover_ts_list, now_ts):
                print(
                    f"[pilot] circuit-breaker: {len(rollover_ts_list)} rollovers em 30s — "
                    "abortando respawn (provável thrash)",
                    file=sys.stderr,
                )
                break

            prepared_restore = (prepared.get("restore") or {}) or {}
            restore_text = (prepared_restore.get("hookSpecificOutput") or {}).get("additionalContext")
            if selected_host == "codex" and isinstance(restore_text, str) and restore_text.strip():
                pending_initial_input = (restore_text.strip() + "\n").encode("utf-8")
            if fork_enabled and selected_host == "claude" and isinstance(restore_text, str) and restore_text.strip():
                pending_extra_args = ["--append-system-prompt", restore_text.strip()]

            current_session_id = new_session_id_from_monitor or next_session_id
            rollover_index += 1
    except KeyboardInterrupt:
        # Ctrl-C between cycles (during relay it goes to the child). Journal,
        # checkpoint and handoff are already durable — exit clean, no traceback.
        print("burnless pilot: interrupted", file=sys.stderr)
        return 130

    return int(rc)


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
