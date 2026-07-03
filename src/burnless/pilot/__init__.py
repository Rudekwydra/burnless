"""Burnless pilot: host-neutral PTY relay and adapter contract."""

from .core import (
    ContextUsage,
    HostAdapter,
    HostCapabilities,
    HostInstallation,
    HostSession,
    PilotEvent,
    build_report,
    discover_hosts,
    resolve_host_adapter,
)
from .events import append_event, append_session_log, normalize_and_append_event, read_events, read_session_log, summarize_run_events, summarize_session_log
from .rollover import (
    arm_rollover,
    claim_handoff,
    evaluate_rollover,
    monitor_rollover_loop,
    monitor_rollover_once,
    prepare_rollover,
    render_restore,
    should_rollover,
)
from .pty_relay import run_pilot
