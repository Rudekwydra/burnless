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
    "restore_pointer_rule": {
        "en": "rule: Refs and Recoverables are POINTERS — Read/grep on demand; never re-read files you already know without a reason.",
    },
    "restore_handoff_header": {
        "en": "## Handoff from my session (written by me, {age} before /clear)",
    },
    "restore_identity_preamble": {
        "en": "I am the direct continuation of session {old_sid_short}. The block below is my working memory, written by me {age} ago, immediately before /clear.",
    },
    "restore_trust_contract": {
        "en": "Contract (handoff_age={age}): claims marked OK/verified below were verified by me pre-clear — treat them as settled. Re-verify ONLY IF: disk contradicts them; the claim covers volatile external state; or handoff_age > 30m. {stale_notice} {pointer_rule_text}",
    },
    "restore_resume_imperative": {
        "en": "You are RESUMING an ongoing session. Continue the work below; do not greet, do not wait for the user.",
    },
    "restore_divergence_warn": {
        "pt-BR": "[burnless] DIVERGÊNCIA: handoff mais novo em {path} (idade {age}m) que o da raiz resolvida {root} — uma janela anterior provavelmente escreveu noutra raiz. NÃO reencarne cego; verifique qual raiz é a correta.",
        "en": "[burnless] DIVERGENCE: fresher handoff at {path} (age {age}m) than under resolved root {root} — a previous window likely wrote to a different root. Do NOT resurrect blind; check which root is correct.",
    },
    "restore_manifest_checkpoint": {
        "en": "- full checkpoint: {path}",
    },
    "restore_manifest_exports": {
        "en": "- exports from previous session: {path}",
    },
    "restore_manifest_epoch_index": {
        "en": "- epoch index (persistent TOC): {path}",
    },
    "restore_manifest_refs": {
        "en": "- Living-doc Refs: already in `path#Lx-y — why [seq N]` format — read only what the current task needs",
    },
    "restore_pending_old_header": {
        "en": "Older exchanges (1-line summary; content in the journal — see Manifest):",
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

    # Get message for language, fallback to en if not present (a key may only
    # ship an "en" variant — see the restore_* payload keys, R2-C).
    template = msg_dict.get(resolved_lang) or msg_dict.get("en")
    if not template:
        raise KeyError(f"No message template for key {key}")
    # The language actually used for this template, after fallback — used
    # below so placeholder defaults never mix an en template with a pt-BR word.
    actual_lang = resolved_lang if resolved_lang in msg_dict else "en"

    # Preserve compatibility with callers that resolve these templates without
    # supplying a concrete restore age.
    if key == "restore_handoff_header":
        kwargs.setdefault("age", "recente" if actual_lang == "pt-BR" else "recent")
    elif key == "restore_trust_contract":
        kwargs.setdefault("age", "recente" if actual_lang == "pt-BR" else "recent")
        kwargs.setdefault("stale_notice", "")
        pointer_messages = MESSAGES["restore_pointer_rule"]
        kwargs.setdefault(
            "pointer_rule_text",
            pointer_messages.get(actual_lang) or pointer_messages["en"],
        )

    # Format with kwargs
    return template.format(**kwargs)
