"""Per-turn classifier: trivial turns → local ollama, non-trivial → maestro.

classify_turn is PURE and deterministic — no LLM/network calls.
local_answer is fail-open: returns None on any error.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

_ACTION_WORDS = re.compile(
    r"\b(implementa|cria|criar|refatora|build|fix|conserta|escreve|delega|delegate"
    r"|roda|executa|deploy|commit|push)\b",
    re.IGNORECASE,
)

_FENCE_RE = re.compile(r"^```", re.MULTILINE)
_SLASH_RE = re.compile(r"^/", re.MULTILINE)


def classify_turn(text: str) -> str:
    """Return 'local' or 'maestro'. Pure, no I/O."""
    if len(text) >= 120:
        return "maestro"
    if _FENCE_RE.search(text):
        return "maestro"
    if _SLASH_RE.search(text):
        return "maestro"
    if _ACTION_WORDS.search(text):
        return "maestro"
    # ends with "?" and has more than 1 sentence after it → maestro
    if text.rstrip().endswith("?"):
        sentences = [s.strip() for s in re.split(r"[.!?]", text) if s.strip()]
        if len(sentences) > 1:
            return "maestro"
    return "local"


def local_answer(
    state,
    user_text: str,
    *,
    ollama_fn: Optional[Callable[[str], str]],
) -> Optional[str]:
    """Build prompt from engine state + user_text, call ollama_fn.

    Returns None if ollama_fn is None or raises (fail-open → caller falls
    through to maestro).
    """
    if ollama_fn is None:
        return None
    try:
        from .engine import assemble_prompt
        prompt = assemble_prompt(state, user_text) + "\n\nResponda em 1-3 frases, direto."
        return ollama_fn(prompt)
    except Exception:
        return None
