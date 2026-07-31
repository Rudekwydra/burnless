"""Content-addressed capsule store for the spine proxy.

Filesystem-first, no TTL: the verbatim of every absorbed exchange lives at
.burnless/proxy/exchanges/<hash>.json for as long as the project does, and
the frozen capsule at .burnless/proxy/capsules/<hash>.txt. Writes are atomic
(tmp + rename); reads and writes fail open.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_HASH_OK = frozenset("0123456789abcdef")


class CapsuleStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.exchanges = self.root / "exchanges"
        self.capsules = self.root / "capsules"
        self.exchanges.mkdir(parents=True, exist_ok=True)
        self.capsules.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe(h: str) -> bool:
        return isinstance(h, str) and 8 <= len(h) <= 64 and set(h) <= _HASH_OK

    def put_original(self, h: str, exchange: list) -> None:
        """Persist the verbatim exchange. Idempotent; never raises."""
        if not self._safe(h):
            return
        path = self.exchanges / f"{h}.json"
        if path.exists():
            return
        try:
            _atomic_write(path, json.dumps(exchange, ensure_ascii=False, indent=1))
        except Exception:
            pass

    def get_original(self, h: str) -> list | None:
        if not self._safe(h):
            return None
        try:
            return json.loads((self.exchanges / f"{h}.json").read_text(encoding="utf-8"))
        except Exception:
            return None

    def put_capsule(self, h: str, text: str) -> None:
        """Freeze a capsule. First write wins — a capsule is never rewritten."""
        if not self._safe(h) or not isinstance(text, str) or not text.strip():
            return
        path = self.capsules / f"{h}.txt"
        if path.exists():
            return
        try:
            _atomic_write(path, text.strip())
        except Exception:
            pass

    def get_capsule(self, h: str) -> str | None:
        if not self._safe(h):
            return None
        try:
            text = (self.capsules / f"{h}.txt").read_text(encoding="utf-8").strip()
            return text or None
        except Exception:
            return None

    def pending(self) -> list[str]:
        """Hashes with a stored original but no capsule yet."""
        try:
            have = {p.stem for p in self.capsules.glob("*.txt")}
            return sorted(p.stem for p in self.exchanges.glob("*.json") if p.stem not in have)
        except Exception:
            return []


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
