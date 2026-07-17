from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .. import recovery
from .core import ContextUsage
from .events import append_session_log, summarize_run_events


def claim_handoff(root: Path, *, host: str, process_instance_id: str, new_session_id: str) -> dict | None:
    return recovery.claim_handoff(root, host=host, process_instance_id=process_instance_id, new_session_id=new_session_id)


def render_restore(
    root: Path,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    new_session_id: str,
    source: str = "clear",
    budget_tokens: int = 2000,
) -> dict | None:
    payload = recovery.render_restore(
        root,
        host=host,
        host_session_id=host_session_id,
        process_instance_id=process_instance_id,
        new_session_id=new_session_id,
        source=source,
        budget_tokens=budget_tokens,
    )
    if payload:
        append_session_log(
            root,
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "host": host,
                "old_session": host_session_id,
                "new_session": new_session_id,
                "process_instance_id": process_instance_id,
                "strategy": "respawn",
                "source": source,
                "context_confidence": "unknown",
                "phase": "restore",
            },
        )
    return payload


def _pending_seed_path() -> Path:
    # BURNLESS_STATE_DIR keeps tests hermetic (never touch the operator's real
    # ~/.burnless/state) and allows relocating global state.
    override = os.environ.get("BURNLESS_STATE_DIR", "").strip()
    base = Path(override) if override else Path.home() / ".burnless" / "state"
    return base / "pending_seed.md"


def _write_pending_seed(target_cwd: Path, restore_context: str) -> None:
    path = _pending_seed_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = f"<!-- burnless-seed-target: {target_cwd} -->\n"
    body = restore_context.strip()
    payload = marker + body + "\n"
    path.write_text(payload, encoding="utf-8")


def prepare_rollover(
    root: Path,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    run_id: str,
    new_session_id: str,
    budget_tokens: int = 2000,
    since_ts: str | None = None,
) -> dict:
    run_state = summarize_run_events(root, run_id, since_ts=since_ts)
    if not run_state.get("idle", False):
        return {"status": "not_ready", "reason": "run_not_idle", "run_state": run_state}
    if since_ts is not None and not run_state.get("saw_active", False):
        return {"status": "not_ready", "reason": "no_turn_since_spawn", "run_state": run_state}

    last_evt = run_state.get("last") or {}
    effective_sid = last_evt.get("host_session_id") or host_session_id
    effective_pid = last_evt.get("process_instance_id") or process_instance_id

    handoff = recovery.write_handoff(
        root,
        host=host,
        host_session_id=effective_sid,
        process_instance_id=effective_pid,
    )
    restore = render_restore(
        root,
        host=host,
        host_session_id=effective_sid,
        process_instance_id=effective_pid,
        new_session_id=new_session_id,
        source="clear",
        budget_tokens=budget_tokens,
    )
    if restore and host == "claude":
        restore_text = restore.get("hookSpecificOutput", {}).get("additionalContext")
        if isinstance(restore_text, str) and restore_text.strip():
            try:
                _write_pending_seed(root.parent if root.name == ".burnless" else root, restore_text)
            except Exception:
                pass
    return {
        "status": "ready" if restore else "not_ready",
        "run_state": run_state,
        "handoff": handoff,
        "restore": restore,
    }


def evaluate_rollover(
    root: Path,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    run_id: str,
    new_session_id: str,
    context_usage: ContextUsage | None = None,
    rollover_at_tokens: int = 40000,
    rollover_at_pct: float = 0.65,
    delta_budget_tokens: int = 2000,
    trusted_confidences: tuple = ("exact",),
    since_ts: str | None = None,
) -> dict:
    run_state = summarize_run_events(root, run_id, since_ts=since_ts)
    if not run_state.get("idle", False):
        return {"should_rollover": False, "reason": "run_not_idle", "run_state": run_state}
    if since_ts is not None and not run_state.get("saw_active", False):
        return {"should_rollover": False, "reason": "no_turn_since_spawn", "run_state": run_state}

    usage = context_usage or ContextUsage(current=None, limit=None, confidence="unknown")
    current = usage.current
    limit = usage.limit
    confidence = usage.confidence or "unknown"

    if current is None or limit is None or confidence == "unknown":
        return {
            "should_rollover": False,
            "reason": "usage_unknown",
            "run_state": run_state,
            "usage": usage,
        }

    if confidence not in trusted_confidences:
        return {
            "should_rollover": False,
            "reason": "usage_estimated_untrusted",
            "run_state": run_state,
            "usage": usage,
        }

    pct = (current / limit) if limit else 0.0
    trigger_by_pct = pct >= float(rollover_at_pct)
    trigger_by_tokens = rollover_at_tokens > 0 and current >= int(rollover_at_tokens)
    if not (trigger_by_pct or trigger_by_tokens):
        return {
            "should_rollover": False,
            "reason": "below_threshold",
            "run_state": run_state,
            "usage": usage,
            "context_pct": pct,
        }

    prepared = prepare_rollover(
        root,
        host=host,
        host_session_id=host_session_id,
        process_instance_id=process_instance_id,
        run_id=run_id,
        new_session_id=new_session_id,
        budget_tokens=delta_budget_tokens,
        since_ts=since_ts,
    )
    return {
        "should_rollover": prepared.get("status") == "ready",
        "reason": "threshold_reached",
        "run_state": run_state,
        "usage": usage,
        "context_pct": pct,
        "prepared": prepared,
        "delta_budget_tokens": delta_budget_tokens,
    }


def should_rollover(
    root: Path,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    run_id: str,
    context_usage: ContextUsage | None = None,
    rollover_at_tokens: int = 40000,
    rollover_at_pct: float = 0.65,
    trusted_confidences: tuple = ("exact",),
    since_ts: str | None = None,
) -> dict:
    run_state = summarize_run_events(root, run_id, since_ts=since_ts)
    if not run_state.get("idle", False):
        return {"should_rollover": False, "reason": "run_not_idle", "run_state": run_state}
    if since_ts is not None and not run_state.get("saw_active", False):
        return {"should_rollover": False, "reason": "no_turn_since_spawn", "run_state": run_state}

    usage = context_usage or ContextUsage(current=None, limit=None, confidence="unknown")
    current = usage.current
    limit = usage.limit
    confidence = usage.confidence or "unknown"
    if current is None or limit is None or confidence == "unknown":
        return {"should_rollover": False, "reason": "usage_unknown", "run_state": run_state, "usage": usage}

    if confidence not in trusted_confidences:
        return {"should_rollover": False, "reason": "usage_estimated_untrusted", "run_state": run_state, "usage": usage}

    pct = (current / limit) if limit else 0.0
    trigger_by_pct = pct >= float(rollover_at_pct)
    trigger_by_tokens = rollover_at_tokens > 0 and current >= int(rollover_at_tokens)
    if not (trigger_by_pct or trigger_by_tokens):
        return {
            "should_rollover": False,
            "reason": "below_threshold",
            "run_state": run_state,
            "usage": usage,
            "context_pct": pct,
        }

    return {
        "should_rollover": True,
        "reason": "threshold_reached",
        "run_state": run_state,
        "usage": usage,
        "context_pct": pct,
    }


def arm_rollover(
    root: Path,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    run_id: str,
    new_session_id: str | None = None,
    context_usage: ContextUsage | None = None,
    rollover_at_tokens: int = 40000,
    rollover_at_pct: float = 0.65,
    trusted_confidences: tuple = ("exact",),
) -> dict:
    decision = should_rollover(
        root,
        host=host,
        host_session_id=host_session_id,
        process_instance_id=process_instance_id,
        run_id=run_id,
        context_usage=context_usage,
        rollover_at_tokens=rollover_at_tokens,
        rollover_at_pct=rollover_at_pct,
        trusted_confidences=trusted_confidences,
    )
    if not decision.get("should_rollover"):
        return {"status": "not_ready", **decision}

    _last = (decision.get("run_state") or {}).get("last") or {}
    _sid = _last.get("host_session_id") or host_session_id
    _pid = _last.get("process_instance_id") or process_instance_id

    handoff = recovery.write_handoff(
        root,
        host=host,
        host_session_id=_sid,
        process_instance_id=_pid,
    )
    append_session_log(
        root,
        {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": run_id,
            "host": host,
            "old_session": host_session_id,
            "new_session": None,
            "process_instance_id": process_instance_id,
            "strategy": "respawn",
            "event": "rollover_armed",
            "reason": decision.get("reason"),
            "new_session": new_session_id,
            "context_confidence": getattr(decision.get("usage"), "confidence", "unknown"),
            "context_pct": decision.get("context_pct"),
        },
    )
    return {"status": "armed", "decision": decision, "handoff": handoff}


def monitor_rollover_once(
    root: Path,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    run_id: str,
    new_session_id: str | None = None,
    context_usage: ContextUsage | None = None,
    rollover_at_tokens: int = 40000,
    rollover_at_pct: float = 0.65,
    delta_budget_tokens: int = 2000,
    trusted_confidences: tuple = ("exact",),
    since_ts: str | None = None,
) -> dict:
    decision = should_rollover(
        root,
        host=host,
        host_session_id=host_session_id,
        process_instance_id=process_instance_id,
        run_id=run_id,
        context_usage=context_usage,
        rollover_at_tokens=rollover_at_tokens,
        rollover_at_pct=rollover_at_pct,
        trusted_confidences=trusted_confidences,
        since_ts=since_ts,
    )
    if not decision.get("should_rollover"):
        return {"status": "not_ready", **decision}

    fresh_session_id = new_session_id or f"{run_id}-fresh"
    armed = arm_rollover(
        root,
        host=host,
        host_session_id=host_session_id,
        process_instance_id=process_instance_id,
        run_id=run_id,
        new_session_id=fresh_session_id,
        context_usage=context_usage,
        rollover_at_tokens=rollover_at_tokens,
        rollover_at_pct=rollover_at_pct,
        trusted_confidences=trusted_confidences,
    )
    prepared = prepare_rollover(
        root,
        host=host,
        host_session_id=host_session_id,
        process_instance_id=process_instance_id,
        run_id=run_id,
        new_session_id=fresh_session_id,
        budget_tokens=delta_budget_tokens,
        since_ts=since_ts,
    )
    return {
        "status": "prepared",
        "decision": decision,
        "armed": armed,
        "prepared": prepared,
        "new_session_id": fresh_session_id,
    }


def monitor_rollover_loop(
    root: Path,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    run_id: str,
    new_session_id: str | None = None,
    context_usage_fn,
    rollover_at_tokens: int = 40000,
    rollover_at_pct: float = 0.65,
    delta_budget_tokens: int = 2000,
    poll_interval_s: float = 0.5,
    stop_event: threading.Event | None = None,
    max_checks: int | None = None,
    trusted_confidences: tuple = ("exact",),
    since_ts: str | None = None,
) -> dict:
    stop_event = stop_event or threading.Event()
    checks = 0
    last = None
    while not stop_event.is_set():
        checks += 1
        usage = context_usage_fn()
        last = monitor_rollover_once(
            root,
            host=host,
            host_session_id=host_session_id,
            process_instance_id=process_instance_id,
            run_id=run_id,
            new_session_id=new_session_id,
            context_usage=usage,
            rollover_at_tokens=rollover_at_tokens,
            rollover_at_pct=rollover_at_pct,
            delta_budget_tokens=delta_budget_tokens,
            trusted_confidences=trusted_confidences,
            since_ts=since_ts,
        )
        if last.get("status") in {"armed", "prepared"}:
            stop_event.set()
            break
        if max_checks is not None and checks >= max_checks:
            break
        time.sleep(max(poll_interval_s, 0.05))
    return {"checks": checks, "last": last}
