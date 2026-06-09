"""Codex subscription (ChatGPT plan) cache mode."""
MECHANISM = "codex_native_session"
KEEPALIVE = False


def warm():
    from .. import warm_session_codex
    return warm_session_codex
