from __future__ import annotations
from math import ceil

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str | bytes) -> int:
    if text is None:
        return 0
    if isinstance(text, bytes):
        n = len(text)
    else:
        n = len(text)
    return ceil(n / CHARS_PER_TOKEN)
