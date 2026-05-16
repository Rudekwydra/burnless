"""Ollama-based Bronze codec for Free tier.

Runs locally via `ollama run qwen2.5-coder` (or configured model). Returns
compressed text + ratio observed. Falls back to passthrough if Ollama
unavailable.
"""
from __future__ import annotations
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

DEFAULT_MODEL = "qwen2.5-coder:7b"
_ENCODE_PROMPT = (
    "Compress the following text to telegraphic style. Keep file paths, "
    "code identifiers, command outputs, and numbers VERBATIM. Strip filler "
    "words, articles, pleasantries. Use abbreviations: imp=implementar, "
    "val=validar, cfg=configuração, doc=documentação, auth=autenticação, "
    "repo=repositório, dir=diretório, arq=arquivo. Output ONLY the compressed "
    "text, no preamble.\n\nTEXT:\n"
)


@dataclass
class CodecResult:
    original_chars: int
    compressed_chars: int
    ratio: float           # original / compressed (>1 means saved)
    compressed_text: str
    used_ollama: bool      # False = passthrough fallback


def is_available(model: str = DEFAULT_MODEL) -> bool:
    """Return True iff ollama binary exists AND server responds."""
    if shutil.which("ollama") is None:
        return False
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def encode(text: str, model: str = DEFAULT_MODEL, timeout: int = 60) -> CodecResult:
    """Compress text via Ollama. Falls back to passthrough on any error."""
    original = len(text)
    if not is_available(model):
        return CodecResult(original, original, 1.0, text, False)
    try:
        result = subprocess.run(
            ["ollama", "run", model],
            input=_ENCODE_PROMPT + text,
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return CodecResult(original, original, 1.0, text, False)
        compressed = result.stdout.strip()
        compressed_chars = len(compressed)
        ratio = original / compressed_chars if compressed_chars > 0 else 1.0
        if ratio < 1.05:
            return CodecResult(original, original, 1.0, text, False)
        return CodecResult(original, compressed_chars, round(ratio, 3), compressed, True)
    except (subprocess.TimeoutExpired, OSError):
        return CodecResult(original, original, 1.0, text, False)
