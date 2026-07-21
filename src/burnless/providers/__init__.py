"""burnless.providers — ask control-plane contracts and adapters.

M1a lands only the contracts (see `.contracts`). The explicit ask adapters
(anthropic CLI, ollama HTTP, codex/openai) arrive in M1b and implement the
`AskAdapter` protocol defined here. Nothing in this package selects a
provider/model or a cache mode on its own: that resolution stays in
`burnless.coreconfig.resolver`, and the cache handler lookup stays in
`burnless.cache_modes`. Adapters consume those results, they do not re-derive
them (handoff invariant 10 — no stack parallel to coreconfig/cache_modes).
"""
from .anthropic_adapter import AnthropicAdapter
from .ollama_adapter import OllamaAdapter
from .codex_adapter import CodexAdapter

_ADAPTERS = {
    "anthropic": AnthropicAdapter,
    "ollama": OllamaAdapter,
    "ollama-local": OllamaAdapter,
    "codex": CodexAdapter,
}


def get_adapter(provider: str):
    """Return a fresh AskAdapter instance for `provider`, or None if unsupported.

    Callers must treat None as a preflight failure (raise before any
    subprocess/HTTP call) — never fall back to a default provider.
    """
    cls = _ADAPTERS.get(provider)
    return cls() if cls else None
