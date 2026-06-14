"""
Burnless capsule compression.

Generates an "operational memory for AI" capsule from a raw agent log + the
JSON summary the agent emitted. The capsule is what feeds back into state.json
and what the next delegation will see — not the raw log.

Capsule compression is fixed and faithful: ~150 chars per field, ≤12 list
items, full paths, dedupe only. There is no mode knob.

A capsule preserves these fields (always present, even if empty):
  objective, status, files, decisions, validations, errors, risks, next

Capsules are NOT human prose. They are key/value records for the next
agent to pick up the work where this one left off.
"""
from __future__ import annotations
import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone

try:
    import anthropic
except ImportError:  # optional dep; tests monkeypatch this
    anthropic = None  # type: ignore[assignment]


def _strip_gemma_channels(text: str) -> str:
    import re as _re
    # gemma-4 harmony "thought" channel: drop everything up to and incl the final <channel|>
    if "<channel|>" in text:
        text = text.rsplit("<channel|>", 1)[1]
    text = _re.sub(r"<\|?channel\|?>", "", text)         # stray channel tokens
    text = _re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)  # any residual ANSI CSI
    return text.strip()


_PER_FIELD = 150
_LIST_ITEMS = 12

_DECISION_PATTERNS = [
    re.compile(r"\b(?:decided|chose|opted|picked|selected)\s+(?:to\s+)?([^\n]{6,140})", re.I),
    re.compile(r"\bDECIDED:\s*([^\n]{6,140})", re.I),
    re.compile(r"\bDECISION:\s*([^\n]{6,140})", re.I),
]
_RISK_PATTERNS = [
    re.compile(r"\b(?:WARN(?:ING)?|RISK|CAUTION|FIXME|TODO|HACK)\b[:\-\s]+([^\n]{6,140})", re.I),
    re.compile(r"\b(?:may|might|could)\s+(?:break|fail|leak|deadlock|conflict)[^\n]{0,80}", re.I),
]
_ERROR_PATTERNS = [
    re.compile(r"\b(?:ERROR|FAILED|Exception|Traceback|stack ?trace)\b[:\-\s]*([^\n]{0,140})", re.I),
    re.compile(r"\bcommand not found:\s*([^\n]{1,80})", re.I),
    re.compile(r"\breturncode[=:\s]+([1-9][0-9]*)", re.I),
]


@dataclass
class Capsule:
    id: str
    objective: str = ""
    status: str = "?"
    files: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    validations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next: str = ""
    mode: str = "faithful"
    tokens: dict = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "mode": self.mode,
            "created_at": self.created_at,
            "objective": self.objective,
            "status": self.status,
            "files": list(self.files),
            "decisions": list(self.decisions),
            "validations": list(self.validations),
            "errors": list(self.errors),
            "risks": list(self.risks),
            "next": self.next,
            "tokens": dict(self.tokens),
        }


def compress(
    *,
    delegation_id: str,
    goal: str,
    summary: dict,
    raw_log: str,
) -> Capsule:
    """Build a faithful capsule from goal + summary + raw_log."""
    from .codec.decoder import _coerce_to_list

    summary = dict(summary or {})
    for field in ("validated", "evidence", "files_touched", "issues"):
        if field in summary:
            summary[field] = _coerce_to_list(summary[field])

    per_field = _PER_FIELD
    max_items = _LIST_ITEMS

    objective = _trim(goal or summary.get("summary", ""), per_field)
    status = str(summary.get("status") or "?").strip().upper() or "?"

    files = _normalize_files(summary.get("files_touched") or [])
    files = _cap_list(files, max_items=max_items, per_item=per_field)

    validations = _cap_list(
        [_trim(s, per_field) for s in (summary.get("validated") or []) if s],
        max_items=max_items,
        per_item=per_field,
    )

    issues_from_summary = [_trim(s, per_field) for s in (summary.get("issues") or []) if s]
    errors_from_log = _extract_errors(raw_log, per_field)
    errors = _cap_list(_dedupe(issues_from_summary + errors_from_log),
                       max_items=max_items, per_item=per_field)

    decisions = _cap_list(
        _dedupe(_extract_decisions(raw_log, per_field)),
        max_items=max_items, per_item=per_field,
    )
    risks = _cap_list(
        _dedupe(_extract_risks(raw_log, per_field)),
        max_items=max_items, per_item=per_field,
    )

    next_step = _trim(summary.get("next") or "", per_field)

    return Capsule(
        id=delegation_id,
        objective=objective,
        status=status,
        files=files,
        decisions=decisions,
        validations=validations,
        errors=errors,
        risks=risks,
        next=next_step,
        mode="faithful",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def write(path: Path, capsule: Capsule) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(capsule.to_dict(), f, indent=2, ensure_ascii=False)


def read(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def measure_savings(raw_log: str, capsule: Capsule, *, chars_per_token: int = 4) -> dict:
    raw_chars = len(raw_log or "")
    cap_chars = len(json.dumps(capsule.to_dict(), ensure_ascii=False))
    raw_tokens = max(0, (raw_chars + chars_per_token - 1) // chars_per_token)
    cap_tokens = max(0, (cap_chars + chars_per_token - 1) // chars_per_token)
    saved = max(0, raw_tokens - cap_tokens)
    ratio = (raw_tokens / cap_tokens) if cap_tokens else 0.0
    return {
        "raw_tokens": raw_tokens,
        "capsule_tokens": cap_tokens,
        "saved_tokens": saved,
        "compression_ratio": round(ratio, 2),
    }


# ----- helpers -----

def _trim(text: str, limit: int) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(it.strip())
    return out


def _cap_list(items: list[str], *, max_items: int, per_item: int) -> list[str]:
    out: list[str] = []
    for it in items:
        t = _trim(it, per_item)
        if t:
            out.append(t)
        if len(out) >= max_items:
            break
    return out


def _normalize_files(items: list) -> list[str]:
    out: list[str] = []
    for f in items:
        if not f:
            continue
        out.append(str(f))
    return _dedupe(out)


def _extract_decisions(log: str, per_field: int) -> list[str]:
    if not log:
        return []
    found: list[str] = []
    for pat in _DECISION_PATTERNS:
        for m in pat.finditer(log):
            if _skip_event_match(log, m):
                continue
            piece = m.group(1) if m.groups() else m.group(0)
            found.append(_trim(piece, per_field))
    return found


def _extract_risks(log: str, per_field: int) -> list[str]:
    if not log:
        return []
    found: list[str] = []
    for pat in _RISK_PATTERNS:
        for m in pat.finditer(log):
            if _skip_event_match(log, m):
                continue
            piece = m.group(1) if m.groups() else m.group(0)
            found.append(_trim(piece, per_field))
    return found


def _extract_errors(log: str, per_field: int) -> list[str]:
    if not log:
        return []
    found: list[str] = []
    for pat in _ERROR_PATTERNS:
        for m in pat.finditer(log):
            if _skip_event_match(log, m):
                continue
            piece = m.group(1) if m.groups() else m.group(0)
            piece = piece.strip()
            if piece:
                found.append(_trim(piece, per_field))
    return found


_CODEX_INTERNAL_MARKERS = (
    "codex_core::",
    "apply_patch",
    "exec_command failed for",
    "Operation not permitted",
    "even though we could not update PATH",
    "Not a git repository",
    "--no-index to compare",
    "if changes introduce conflict markers",
    "highlight <kind>",
    ".burnless/logs/",
    "\"stack trace\", \"exception\"",
    "Use codex to inspect the error log",
    "Worker failed before saving a capsule",
    "Codex cannot access session files",
    "confirmed d005 has no ANTHROPIC_API_KEY false error",
    "# returncode: 1",
    "\"returncode=1\"",
    "returncode = 130",
    "(most recent call last)",
)
_PYTHON_CODE_MARKERS = (
    "file=sys.stderr",
    "print(",
    "re.compile(",
    "re.I",
    "raise SystemExit",
    "raise ",
    "def ",
    "class ",
)
_PYTHON_CODE_PREFIX = re.compile(
    r"^(?:[+\-]\s*)?(?:if|elif|else|for|while|try|except|finally|with|return|import|from)\b"
)
_PYTHON_FILE_LINE_PREFIX = re.compile(r"^(?:\.?/)?[\w./-]+\.py:\d+:\s*")


def _skip_event_match(log: str, match: re.Match) -> bool:
    return _looks_like_non_event_line(_line_for_match(log, match))


def _line_for_match(log: str, match: re.Match) -> str:
    start = log.rfind("\n", 0, match.start()) + 1
    end = log.find("\n", match.end())
    if end == -1:
        end = len(log)
    return log[start:end]


def _looks_like_non_event_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False

    if re.search(r"\bapply_patch failed\b", s, re.I):
        return False

    if any(marker in s for marker in _CODEX_INTERNAL_MARKERS):
        return True
    if s.count("[stderr]") + s.count("[stdout]") >= 2:
        return True

    code = s
    if code.startswith("[stderr]"):
        code = code[len("[stderr]"):].lstrip()
    if code.startswith(("│", "|", ">")):
        code = code[1:].lstrip()
    if code.startswith(("+", "-")):
        code = code[1:].lstrip()
    code = _PYTHON_FILE_LINE_PREFIX.sub("", code)
    if ":" in code:
        prefix, rest = code.split(":", 1)
        if prefix.strip().isdigit():
            code = rest.lstrip()

    if re.fullmatch(r"[\"',\s]*", code):
        return True
    if re.fullmatch(r"\"[^\"]*\"\s*,\s*", code):
        return True
    if code.count("\"") >= 3 and "," in code:
        return True

    if any(marker in code for marker in _PYTHON_CODE_MARKERS):
        return True
    if re.search(r"^as\s+\w+\s*:$", code) or re.search(r"\bas\s+\w+\s*:$", code):
        return True
    if code.startswith(("\"", "'", "f\"", "f'", "r\"", "r'")):
        return True
    if _PYTHON_CODE_PREFIX.search(code):
        return True
    if re.search(r"^[A-Za-z_]\w*\s*=", code):
        return True
    if code.endswith(")") and re.search(r"\b[a-zA-Z_][\w.]*\(.*\)$", code):
        return True

    return False


_SLUG_KEEP = re.compile(r"[a-z0-9._/\- ]+")

def _slugify_phrase(text: str, *, max_words: int) -> str:
    if not text:
        return ""
    s = text.lower()
    s = " ".join(s.split())
    parts = [p for p in s.split(" ") if p]
    if len(parts) > max_words:
        parts = parts[:max_words]
    s = " ".join(parts)
    keep = "".join(_SLUG_KEEP.findall(s))
    return keep.strip()


def compress_transcript(
    text: str,
    *,
    mode: str = "balanced",
    session_context: list[dict] | None = None,
    encoder: dict | None = None,
) -> tuple[str, dict]:
    """
    4-layer compression of arbitrary text (chat transcript, briefing, session).
    Returns (packed_capsule, stats).

    session_context: list of {'raw': str, 'compressed': str} from prior turns.
    Haiku sees this as cache and builds its own glossary implicitly.
    v2 capsule keys are kept in the local in-memory keyring by default; they are
    not embedded in the capsule. v1 capsules with embedded keys are legacy only.
    """
    import secrets
    from .codec.cipher import generate_key, encode as cipher_encode, pack
    from . import config

    session_id = secrets.token_hex(6)
    key = generate_key()

    # Layer 1: deterministic filler strip.
    _UNIVERSAL_FILLERS = (
        "please", "could you", "would you", "can you", "thank you", "thanks",
        "great job", "perfect", "if possible", "if you can", "when you get a chance",
        "I need you to", "I want you to", "I'd like you to",
    )
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        if any(low == f.lower() for f in _UNIVERSAL_FILLERS):
            continue
        for f in _UNIVERSAL_FILLERS:
            stripped = stripped.replace(f + " ", "").replace(f + ", ", "")
        lines.append(stripped)
    minified = "\n".join(lines)

    if mode == "light":
        ciphertext = cipher_encode(minified, key)
        capsule = pack(session_id, key, ciphertext)
        stats = {
            "session_id": session_id,
            "original_chars": len(text),
            "capsule_chars": len(minified),
            "ratio": round((1 - len(minified) / max(len(text), 1)) * 100, 1),
            "mode": mode,
        }
        return capsule, stats

    # Layer 2: semantic compression via cache-emergent glossary (provider-selectable).
    _SYSTEM_PROMPT = (
        "You are a lossless semantic compressor. "
        "Compress the input into a dense capsule preserving ALL decisions, "
        "tasks, context, and next steps. Use consistent abbreviations — "
        "infer them from prior turns in this conversation if any. "
        "Output ONLY the compressed capsule. No preamble, no explanation."
    )
    try:
        enc = encoder or {}
        provider = (enc.get("provider") or "anthropic").strip()
        model = (enc.get("model") or config.HAIKU_MODEL)
        if provider == "ollama-local":
            ctx_block = ""
            if session_context:
                for ctx in (session_context or [])[-6:]:
                    ctx_block += f"RAW:\n{ctx['raw']}\nCOMPRESSED:\n{ctx['compressed']}\n\n"
            combined = _SYSTEM_PROMPT + "\n\n" + ctx_block + "INPUT:\n" + minified
            _payload = json.dumps({"model": model, "prompt": combined, "stream": False}).encode()
            _req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=_payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(_req, timeout=300) as _resp:
                _data = json.loads(_resp.read().decode())
            compressed = (_data.get("response") or "").strip()
            compressed = _strip_gemma_channels(compressed)
            compressed = re.sub(r"^```[^\n]*\n?", "", compressed)
            compressed = re.sub(r"\n?```$", "", compressed).strip()
        else:
            client = anthropic.Anthropic()
            messages = []
            if session_context:
                for ctx in (session_context or [])[-6:]:
                    messages.append({"role": "user", "content": ctx["raw"]})
                    messages.append({"role": "assistant", "content": ctx["compressed"]})
            messages.append({"role": "user", "content": minified})
            response = client.messages.create(
                model=model,
                max_tokens=2048,
                system=_SYSTEM_PROMPT,
                messages=messages,
            )
            compressed = response.content[0].text.strip()
        if len(compressed) >= len(minified):
            compressed = minified
    except Exception:
        compressed = minified

    # Layers 3 and 4: lightweight capsule envelope plus base64.
    ciphertext = cipher_encode(compressed, key)
    capsule = pack(session_id, key, ciphertext)

    stats = {
        "session_id": session_id,
        "original_chars": len(text),
        "capsule_chars": len(compressed),
        "ratio": round((1 - len(compressed) / max(len(text), 1)) * 100, 1),
        "mode": mode,
    }
    return capsule, stats
