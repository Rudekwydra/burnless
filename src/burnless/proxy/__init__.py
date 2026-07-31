"""Spine proxy — rolling memory on the live model path.

A minimal Anthropic-compatible reverse proxy that carries already-processed
exchanges forward as frozen capsules (append-only spine) instead of verbatim
history. See _design/SPINE_PROXY_2026-07-31.md.
"""

from .spine import transform, split_exchanges, exchange_hash, approx_tokens  # noqa: F401
from .store import CapsuleStore, resolve_ref  # noqa: F401

# server.serve / server.SpineProxy are imported lazily by callers — pulling
# httpx into every `import burnless.proxy` would tax CLI startup for nothing.
