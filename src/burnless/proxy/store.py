"""Content-addressed capsule store for the spine proxy.

Filesystem-first, no TTL: the verbatim of every absorbed exchange lives at
.burnless/proxy/exchanges/<hash>.json for as long as the project does, and
the frozen capsule at .burnless/proxy/capsules/<hash>.txt. First write wins
at the kernel level (link(2), not check-then-replace) — a frozen capsule can
never change under a request that already shipped it. Reads and writes fail
open.

privacy.raw_retention: none is honored: originals then live only in a
bounded in-process buffer (enough for the async compressor to do its job)
and nothing verbatim ever touches disk.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

_HASH_OK = frozenset("0123456789abcdef")
_MEM_ORIGINALS_MAX = 512


class CapsuleStore:
    def __init__(self, root: Path, *, raw_retention: str = "plain"):
        self.root = Path(root)
        self.raw_retention = raw_retention
        self.exchanges = self.root / "exchanges"
        self.capsules = self.root / "capsules"
        self.exchanges.mkdir(parents=True, exist_ok=True)
        self.capsules.mkdir(parents=True, exist_ok=True)
        self._mem: OrderedDict[str, list] = OrderedDict()
        self._mem_lock = threading.Lock()

    @staticmethod
    def _safe(h: str) -> bool:
        return isinstance(h, str) and 8 <= len(h) <= 64 and set(h) <= _HASH_OK

    def put_original(self, h: str, exchange: list) -> None:
        """Persist the verbatim exchange. Idempotent; never raises."""
        if not self._safe(h):
            return
        if self.raw_retention == "none":
            with self._mem_lock:
                self._mem[h] = exchange
                self._mem.move_to_end(h)
                while len(self._mem) > _MEM_ORIGINALS_MAX:
                    self._mem.popitem(last=False)
            return
        try:
            _create_exclusive(self.exchanges / f"{h}.json", json.dumps(exchange, ensure_ascii=False, indent=1))
        except Exception:
            pass

    def get_original(self, h: str) -> list | None:
        if not self._safe(h):
            return None
        try:
            return json.loads((self.exchanges / f"{h}.json").read_text(encoding="utf-8"))
        except Exception:
            with self._mem_lock:
                return self._mem.get(h)

    def put_capsule(self, h: str, text: str) -> None:
        """Freeze a capsule. First write wins — enforced by link(2), so two
        processes sharing the root can never rewrite a shipped capsule."""
        if not self._safe(h) or not isinstance(text, str) or not text.strip():
            return
        try:
            _create_exclusive(self.capsules / f"{h}.txt", text.strip())
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
        """Hashes with a stored original but no capsule yet — the restart
        backlog the server drains into the compressor on startup."""
        try:
            have = {p.stem for p in self.capsules.glob("*.txt")}
            return sorted(p.stem for p in self.exchanges.glob("*.json") if p.stem not in have)
        except Exception:
            return []


def resolve_ref(root: Path, ref: str) -> dict | None:
    """Recover one absorbed exchange by its capsule ref.

    ``ref`` is the hash a capsule ends with, accepted with or without the
    ``ref:`` prefix. Reads straight from disk (no directory creation — a
    lookup must not scaffold state). Returns ``{"ref", "capsule",
    "exchange"}`` with whatever half exists (the other is None), or None
    when nothing is on disk for that hash. Never raises.
    """
    h = str(ref or "").strip()
    if h.startswith("ref:"):
        h = h[4:]
    if not CapsuleStore._safe(h):
        return None
    root = Path(root)
    capsule = None
    try:
        capsule = (root / "capsules" / f"{h}.txt").read_text(encoding="utf-8").strip() or None
    except Exception:
        pass
    exchange = None
    try:
        exchange = json.loads((root / "exchanges" / f"{h}.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    if capsule is None and exchange is None:
        return None
    return {"ref": h, "capsule": capsule, "exchange": exchange}


def _create_exclusive(path: Path, text: str) -> None:
    """Atomic create-if-absent: write a temp file, link(2) it into place.
    EEXIST means someone else won the race — their write stands."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
