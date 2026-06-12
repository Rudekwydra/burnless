"""Per-turn classifier: trivial turns → local ollama, non-trivial → maestro.

classify_turn is PURE and deterministic — no LLM/network calls.
local_answer is fail-open: returns None on any error.
parse_chat_command is PURE — parses /slash commands from the chat REPL.
"""
from __future__ import annotations

import re
from typing import Callable, Optional, Tuple, Union

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


def parse_chat_command(line: str) -> Union[Tuple[str, object], None]:
    """Parse a /slash command from the chat REPL.

    Returns:
        ("router", True|False)     for /router on|off
        ("expand", True|False)     for /expand on|off
        ("rollover", int)          for /rollover N
        ("status", None)           for /status
        ("help", None)             for /help
        ("error", str)             for malformed commands (e.g. /rollover abc)
        None                       for non-command lines or /exit /quit /q
    """
    stripped = line.strip()
    if not stripped.startswith("/"):
        return None
    # preserve /exit /quit /q for caller
    if stripped in {"/exit", "/quit", "/q"}:
        return None

    lower = stripped.lower()

    if lower in {"/router on", "/router off"}:
        return ("router", lower.endswith(" on"))

    if lower in {"/expand on", "/expand off"}:
        return ("expand", lower.endswith(" on"))

    if lower.startswith("/rollover "):
        rest = stripped[len("/rollover "):].strip()
        try:
            n = int(rest)
        except ValueError:
            return ("error", f"/rollover expects integer >=0, got: {rest!r}")
        if n < 0:
            return ("error", f"/rollover expects integer >=0, got: {n}")
        return ("rollover", n)

    if lower == "/status":
        return ("status", None)

    if lower == "/help":
        return ("help", None)

    return ("unknown", stripped)


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
