"""Internationalization: simple dict-based message resolution.

lang resolution: any non "pt-BR" falls to "en"; missing key in lang falls to "en".
Placeholders use named format: msg("key", lang, token_count=252000).
BURNLESS_LANG env overrides config when config not available (e.g. early guard checks).
"""

import os


MESSAGES: dict[str, dict[str, str]] = {
    "footer_worker_tokens": {
        "pt-BR": "worker {orig_fmt} tok brutos → {comp_fmt} no contexto {ratio_fmt} · {cost_part}",
        "en": "worker {orig_fmt} raw tokens → {comp_fmt} in context {ratio_fmt} · {cost_part}",
    },
    "footer_input_avoided_local": {
        "pt-BR": "input evitado worker local $0",
        "en": "input avoided worker local $0",
    },
    "footer_input_avoided_est": {
        "pt-BR": "input evitado est. ${avoided_cost:.2f}",
        "en": "input avoided est. ${avoided_cost:.2f}",
    },
    "praise_tokens_compressed": {
        "pt-BR": "🏆 {ratio:.0f}× — {orig_fmt} tok brutos viraram {comp_fmt} de contexto. Spec apertada pagou.",
        "en": "🏆 {ratio:.0f}× — {orig_fmt} raw tokens became {comp_fmt} in context. Tight spec paid off.",
    },
    "guard_nested_delegation": {
        "pt-BR": "burnless: worker context — re-delegacao bloqueada. Execute a task diretamente; opt-in explicito: BURNLESS_ALLOW_NESTED=1",
        "en": "burnless: worker context — nested delegation blocked. Execute task directly; explicit opt-in: BURNLESS_ALLOW_NESTED=1",
    },
}


def msg(key: str, lang: str, **kwargs) -> str:
    """Resolve message by key and language.

    Args:
        key: message key (e.g. "footer_worker_tokens")
        lang: language code ("pt-BR", "en", or any other → falls to "en")
        **kwargs: named placeholders for the message format string

    Returns:
        Formatted message string.

    Raises:
        KeyError: if key not found in MESSAGES.
    """
    if key not in MESSAGES:
        raise KeyError(f"Unknown message key: {key}")

    # Resolve language: pt-BR or fallback to en
    resolved_lang = "pt-BR" if lang == "pt-BR" else "en"
    msg_dict = MESSAGES[key]

    # Get message for language, fallback to en if not present
    template = msg_dict.get(resolved_lang) or msg_dict.get("en")
    if not template:
        raise KeyError(f"No message template for key {key}")

    # Format with kwargs
    return template.format(**kwargs)
