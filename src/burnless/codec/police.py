from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import anthropic

from .glossary_loader import load_glossary


def maybe_police(
    raw_message: str,
    capsule: str,
    confidence: float,
    *,
    project_root: Path | None = None,
    model: str = "claude-sonnet-4-6",
    client: anthropic.Anthropic | None = None,
) -> tuple[str, bool]:
    """
    Returns (final_capsule, was_corrected).
    Runs only when confidence < 0.8 or BURNLESS_POLICE=1.
    """
    if confidence >= 0.8 and os.environ.get("BURNLESS_POLICE") != "1":
        return capsule, False

    prompt = "\n\n".join(
        [
            (
                "Voce e o Police do Burnless. Recebeu uma mensagem bruta e sua "
                "versao encoded em capsule.\n"
                "Verifique se a capsule preserva o significado da mensagem original.\n"
                "Se sim, responda apenas: OK\n"
                "Se nao, responda apenas com a capsule corrigida (sem explicacao)."
            ),
            "[GLOSSARY]",
            load_glossary(project_root),
            "[RAW]",
            raw_message,
            "[CAPSULE]",
            capsule,
        ]
    )
    try:
        client = client or anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return capsule, False

    text = _response_text(response)
    if text.strip().upper() == "OK":
        return capsule, False
    if not text.strip():
        return capsule, False
    return text.strip(), True


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()
