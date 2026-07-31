"""Exchange → capsule compression, off the request path.

The capsule follows the burnless principle: concept, not phrase — what a
human would retell, not what was said. Default codec is deterministic
extractive (salient lines survive verbatim, bulk is dropped); `proxy.codec:
ollama` upgrades to the local model already used by the offload hook, with
the extractive result as fail-open fallback and as the size validator's
floor. A capsule is accepted only if it is materially smaller than the
exchange it replaces.

Runs in a daemon thread fed by the server; a request never waits for a
capsule.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import urllib.request
from typing import Any

from .spine import approx_tokens as _approx_tokens

_WS = re.compile(r"\s+")
_SALIENT = re.compile(
    r"(error|fail|traceback|exception|fix|decid|because|instead|must|nunca|sempre|"
    r"[\w./~-]+\.[a-z]{1,6}\b|https?://|\b\d{2,}\b)",
    re.IGNORECASE,
)

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_PROMPT = (
    "Compress this finished chat exchange into ONE dense line a human would "
    "use to retell it: who asked what, what was concluded/done, names of "
    "files, decisions, errors. No preamble, no markdown, max 400 chars.\n\n"
)


def _texts(exchange: list) -> tuple[list[str], list[str], list[str]]:
    """Split an exchange into (user_texts, assistant_texts, tool_notes)."""
    users: list[str] = []
    assistants: list[str] = []
    tools: list[str] = []
    for msg in exchange:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str):
                (users if role == "user" else assistants).append(block["text"])
            elif btype == "tool_use":
                tools.append(str(block.get("name", "tool")))
            elif btype in ("tool_result", "thinking", "redacted_thinking"):
                pass  # bulk / private by definition; the verbatim lives on disk
            elif btype in ("image", "document"):
                tools.append(btype)
    return users, assistants, tools


def _gist(texts: list[str], budget: int) -> str:
    """Deterministic extract: first line, salient lines, last line — squeezed."""
    joined = "\n".join(t for t in texts if t and t.strip())
    lines = [ln.strip() for ln in joined.splitlines() if ln.strip()]
    if not lines:
        return ""
    picked: list[str] = [lines[0]]
    for ln in lines[1:-1]:
        if _SALIENT.search(ln):
            picked.append(ln)
    if len(lines) > 1:
        picked.append(lines[-1])
    seen: set[str] = set()
    unique = [ln for ln in picked if not (ln in seen or seen.add(ln))]
    out = _WS.sub(" ", " | ".join(unique))
    return out[: budget - 1] + "…" if len(out) > budget else out


def compress_exchange(exchange: list, h: str, *, max_chars: int = 700, codec: str = "extractive") -> str | None:
    """Build the frozen capsule for one exchange. Never raises."""
    try:
        users, assistants, tools = _texts(exchange)
        side = max(120, (max_chars - 60) // 2)
        user_gist, asst_gist = _gist(users, side), _gist(assistants, side)
        if not user_gist and not asst_gist:
            if not tools:
                return None  # nothing to retell at all; the exchange stays verbatim
            # Non-text exchange (image/document/tool loop): the promised
            # placeholder, so one such exchange can never stall the spine.
            user_gist, asst_gist = "[non-text exchange]", "[see ref]"
        extractive = f"U: {user_gist} :: A: {asst_gist}"
        if tools:
            uniq = sorted(set(tools))
            extractive += f" :: T: {','.join(uniq[:8])}"
        capsule = extractive
        if codec == "ollama":
            better = _ollama(exchange, max_chars)
            if better and len(better) < len(extractive) * 1.5:
                capsule = better
        capsule = _WS.sub(" ", capsule).strip()
        if len(capsule) > max_chars:
            capsule = capsule[: max_chars - 1] + "…"
        # Both sides in the same units (approx tokens over the same
        # serialization) — raw len() vs escaped JSON once inverted this gate
        # for non-ASCII conversations.
        original = json.dumps(exchange, ensure_ascii=False, separators=(",", ":"))
        if not capsule or _approx_tokens(capsule) >= _approx_tokens(original):
            return None
        return f"{capsule} [ref:{h}]"
    except Exception:
        return None


def _ollama(exchange: list, max_chars: int, *, model: str | None = None, timeout: float = 30.0) -> str | None:
    try:
        users, assistants, _ = _texts(exchange)
        raw = ("USER:\n" + "\n".join(users) + "\n\nASSISTANT:\n" + "\n".join(assistants))[:20000]
        payload = json.dumps(
            {
                "model": model or "hf.co/unsloth/gemma-4-E4B-it-qat-GGUF:UD-Q4_K_XL",
                "prompt": OLLAMA_PROMPT + raw,
                "stream": False,
                "options": {"num_predict": max(64, max_chars // 3)},
            }
        ).encode("utf-8")
        req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = (json.loads(resp.read().decode("utf-8")).get("response") or "").strip()
        return text or None
    except Exception:
        return None


class CompressorThread:
    """Daemon worker: hash in, frozen capsule on disk out."""

    def __init__(self, store: Any, *, codec: str = "extractive", max_chars: int = 700):
        self.store = store
        self.codec = codec
        self.max_chars = max_chars
        self._q: queue.Queue[str] = queue.Queue()
        self._queued: set[str] = set()
        self._failed: set[str] = set()  # negative cache: a doomed exchange is retried once, not every turn
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True, name="burnless-spine-compressor")

    def start(self) -> None:
        self._thread.start()

    def enqueue(self, hashes: list[str]) -> None:
        with self._lock:
            for h in hashes:
                if h not in self._queued and h not in self._failed:
                    self._queued.add(h)
                    self._q.put(h)

    def _run(self) -> None:
        while True:
            h = self._q.get()
            try:
                if self.store.get_capsule(h) is not None:
                    continue
                exchange = self.store.get_original(h)
                if exchange is None:
                    continue
                capsule = compress_exchange(exchange, h, max_chars=self.max_chars, codec=self.codec)
                if capsule:
                    self.store.put_capsule(h, capsule)
                else:
                    with self._lock:
                        self._failed.add(h)
            except Exception:
                pass  # fail-open; the exchange simply stays verbatim
            finally:
                with self._lock:
                    self._queued.discard(h)
