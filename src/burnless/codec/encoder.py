from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import anthropic

from .glossary_loader import load_glossary

DEFAULT_ENCODER_MODEL = "claude-haiku-4-5"
_TIER_PREFIXES = ("gld", "slv", "brz", "dia", "raw:", "~gld", "+slv")
_ABBREV = {
    # Generic vocabulary abbreviations.
    # Tenant-specific terms (project names, frameworks, customer slugs)
    # belong in tenant_glossary.yaml — see _design/maestro_v1/glossary.md.
    "feature": "F",
    "funcionalidade": "F",
    "implementar": "imp",
    "implementa": "imp",
    "implementação": "impl",
    "validar": "val",
    "validação": "val",
    "deletar": "del",
    "arquivo": "arq",
    "diretório": "dir",
    "repositório": "repo",
    "configuração": "cfg",
    "configurar": "cfg",
    "documentação": "doc",
    "documentar": "doc",
    "autenticação": "auth",
    "autenticar": "auth",
    "em paralelo": "||",
    "paralelamente": "||",
    "ao mesmo tempo": "||",
}
_FILLERS = (
    "por favorzinho",
    "por favor",
    "você pode",
    "voce pode",
    "vc pode",
    "gostaria que",
    "gostaria de",
    "seria possível",
    "seria possivel",
    "poderia",
    "consegue",
    "conseguiria",
    "me ajuda a",
    "me ajude a",
    "me ajudaria a",
    "preciso que você",
    "preciso que vc",
    "quero que você",
    "quero que vc",
    "pode fazer",
    "consegue fazer",
    "obrigado",
    "obrigada",
    "valeu",
    "vlw",
    "tudo bem?",
    "tudo bom?",
    "tá bom?",
    "ta bom?",
    "please",
    "could you",
    "would you",
    "can you",
    "I need you to",
    "I want you to",
    "I'd like you to",
    "thank you",
    "thanks",
    "great",
    "perfect",
    "if possible",
    "if you can",
    "when you get a chance",
)
_STANDALONE_FILLERS = ("então", "aí", "né", "ok", "certo")

FEW_SHOTS = """
user: "check the state of delegations d010 and d011 and report back"
capsule: "raw:check state d010 d011 and report"

user: "implement front 01 and 02 in parallel"
capsule: "raw:imp app/F01 and app/F02 parallel"

user: "I had an idea"
capsule: "raw:had an idea"

user: "what's the status of the auth subsystem?"
capsule: "raw:status app/auth?"

user: "and the dashboard?"
capsule: "raw:status app/dash?"

user: "for F7 I need schema, router and prompts — do all of it"
capsule: "gld del→T? slv imp app/F7 :: schema+router+prompts"

user: "full audit of the dashboard service before shipping to prod"
capsule: "gld del→T? gld aud app/dash :: full audit pre-deploy"

user: "ok"
capsule: "raw:ok"
""".strip()


def encode(
    raw_message: str,
    *,
    project_root: Path | None = None,
    model: str = DEFAULT_ENCODER_MODEL,
    client: anthropic.Anthropic | None = None,
) -> tuple[str, float]:
    """Encode PT-BR raw text into a Burnless capsule with Haiku."""
    try:
        compressed = minify(raw_message) or raw_message
    except Exception:
        compressed = raw_message
    prompt = _build_prompt(compressed, project_root=project_root)
    try:
        client = client or anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return _fallback_capsule(raw_message), 0.6

    text = _response_text(response)
    capsule = _extract_capsule_text(text)
    if not capsule:
        return _fallback_capsule(raw_message), 0.6
    return _wrap_capsule_lines(capsule), _score_capsule(capsule)


def minify(text: str) -> str:
    """Deterministic lossless pre-compression before semantic encoding.

    Removes filler phrases, normalizes whitespace, applies glossary
    abbreviations, collapses punctuation. Zero API calls. Zero latency.
    """
    try:
        abbrev = _glossary_abbrev()
        parts = re.split(r"(```.*?```)", text or "", flags=re.DOTALL)
        for i, part in enumerate(parts):
            if part.startswith("```"):
                continue
            part = _strip_markdown(part)
            part = _strip_fillers(part)
            part = re.sub(r"(?i)(?<!\w)checar(?!\w)", "checa", part)
            part = _normalize_ws(part)
            for full, short in sorted(abbrev.items(), key=lambda x: len(x[0]), reverse=True):
                pattern = r"(?<!\w)" + re.escape(full) + r"(?!\w)"
                part = re.sub(pattern, short, part, flags=re.IGNORECASE)
            part = _collapse_punctuation(part)
            parts[i] = _normalize_ws(part)
        return _join_minified_parts(parts).strip()
    except Exception:
        return text


def _join_minified_parts(parts: list[str]) -> str:
    out = ""
    for part in parts:
        if not part:
            continue
        if part.startswith("```") and out and not out.endswith("\n"):
            out += "\n"
        if out.endswith("```") and not part.startswith("\n"):
            out += "\n"
        out += part
    return out


def _strip_markdown(text: str) -> str:
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    return re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", text)


def _strip_fillers(text: str) -> str:
    filler_alt = "|".join(re.escape(f) for f in sorted(_FILLERS, key=len, reverse=True))
    standalone_alt = "|".join(re.escape(f) for f in _STANDALONE_FILLERS)
    starts = re.compile(rf"(?is)(^|[.!?\n]\s*)({filler_alt})(?=$|[\s,;:.!?-])[,;\s:.-]*")
    ends = re.compile(rf"(?is)[,;\s:.-]*\b({filler_alt})[.!?]*\s*($|[.!?\n])")
    trail = re.compile(rf"(?is)[,\s:;-]*\b({standalone_alt})\??\s*($|[.!?\n])")
    standalone = re.compile(rf"(?is)(^|[.!?\n]\s*)({standalone_alt})\??[,\s:;-]*")
    previous = None
    while text != previous:
        previous = text
        text = starts.sub(r"\1", text)
        text = ends.sub(r"\2", text)
        text = standalone.sub(r"\1", text)
        text = trail.sub(r"\2", text)
    return text


def _normalize_ws(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _collapse_punctuation(text: str) -> str:
    text = re.sub(r"\.{3,}", "…", text)
    text = re.sub(r"!{2,}", "!", text)
    text = re.sub(r"\?{2,}", "?", text)
    text = re.sub(r"\s*,\s*,\s*", ", ", text)
    return re.sub(r"\s+,\s*", ", ", text)


def _glossary_abbrev() -> dict[str, str]:
    pairs = dict(_ABBREV)
    glossary = load_glossary(None)
    for short, full in re.findall(r"(?m)^\s*-\s*`([^`]+)`\s*=\s*([^\n(]+)", glossary):
        full = full.strip()
        if full and len(short.strip()) <= 12:
            pairs.setdefault(full, short.strip())
    return pairs


def _build_prompt(raw_message: str, *, project_root: Path | None = None) -> str:
    return "\n\n".join(
        [
            "[GLOSSARY]",
            load_glossary(project_root),
            "[FEW_SHOTS]",
            FEW_SHOTS,
            "[USER]",
            raw_message,
            "[CAPSULE]",
        ]
    )


def _wrap_capsule_lines(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines() or [text]:
        line = re.sub(r"\s+", " ", line).strip()
        while len(line) > 80:
            cut = line.rfind(" ", 0, 80)
            if cut <= 4:
                cut = 80
            lines.append(line[:cut].rstrip())
            line = "raw:" + line[cut:].strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _fallback_capsule(raw_message: str) -> str:
    compact = " ".join((raw_message or "").strip().split())
    capsule = f"raw:{compact}" if not compact.startswith("raw:") else compact
    return _wrap_capsule_lines(capsule)


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def _extract_capsule_text(text: str) -> str:
    cleaned_lines: list[str] = []
    for line in (text or "").strip().splitlines():
        line = _clean_response_line(line)
        if not line:
            continue
        if any(line.startswith(prefix) for prefix in _TIER_PREFIXES) or "::" in line:
            cleaned_lines.append(line)
    if cleaned_lines:
        return "\n".join(cleaned_lines)
    first_line = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    return _clean_response_line(first_line)


def _clean_response_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^capsule\s*:\s*", "", line, flags=re.IGNORECASE)
    return line.strip().strip('"').strip("'").strip()


def _score_capsule(text: str) -> float:
    first_line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if any(first_line.startswith(p) for p in _TIER_PREFIXES):
        return 1.0
    if "::" in first_line:
        return 0.9
    return 0.7
