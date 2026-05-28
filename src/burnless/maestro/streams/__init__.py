from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class NormalizedEvent:
    kind: Literal["think_delta", "text_delta", "usage", "done"]
    text: str = ""
    usage: dict | None = None
