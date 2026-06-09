from importlib import import_module


def get(mode_name: str):
    """Return the cache-mode handler module for a DEFAULT_CACHE_MODES key."""
    _MAP = {
        "anthropic_subscription": ".anthropic_subscription",
        "anthropic_api": ".anthropic_api",
        "codex_subscription": ".codex_subscription",
        "codex_api": ".codex_api",
        "gemini_subscription": ".gemini_subscription",
        "gemini_api": ".gemini_api",
        "none": ".none",
    }
    rel = _MAP.get(mode_name, ".none")
    return import_module(__name__ + rel)
