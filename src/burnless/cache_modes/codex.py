"""Codex cache mode. Native codex session caching; warm pool via warm_session_codex."""
MECHANISM = "codex_native_session"
KEEPALIVE = False


def warm():
    from .. import warm_session_codex
    return warm_session_codex
