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
[TONE_DETECTION]
Detect the tone of THIS specific user message (per-message, not a user profile —
same user can switch tones turn to turn) and tag the capsule:
  [tone:X,lang:Y]

Tone tags (use closest match):
  formal      — polite, full forms, no contractions
  casual      — informal, contractions, light
  mano        — SP/RJ slang ("véi", "mano", "tipo", "treta")
  diminutivo  — "cadinho", "tipo assim", "parada", "treco"
  telegraphic — no articles, just key tokens
  code        — technical, file paths, function names, imperative
  emotive     — with emojis, "❤️", "!" or affection markers
  meme        — "kkk", "lol", internet humor
  bug_report  — stack trace style, line/file references
  tentative   — "será que", "e se", "ahn", proposing not asserting
  imperative  — direct command, no padding
  code_review — PR/review register

Lang tags: pt | en | mix

[FEW_SHOTS]

user: "check the state of delegations d010 and d011 and report back"
capsule: "raw:check state d010 d011 report [tone:imperative,lang:en]"

user: "implement front 01 and 02 in parallel"
capsule: "raw:imp app/F01 and app/F02 parallel [tone:imperative,lang:en]"

user: "I had an idea"
capsule: "raw:had an idea [tone:casual,lang:en]"

user: "what's the status of the auth subsystem?"
capsule: "raw:status app/auth? [tone:casual,lang:en]"

user: "and the dashboard?"
capsule: "raw:status app/dash? [tone:casual,lang:en]"

user: "for F7 I need schema, router and prompts — do all of it"
capsule: "gld del→T? slv imp app/F7 :: schema+router+prompts [tone:imperative,lang:en]"

user: "full audit of the dashboard service before shipping to prod"
capsule: "gld del→T? gld aud app/dash :: full audit pre-deploy [tone:imperative,lang:en]"

user: "ok"
capsule: "raw:ok [tone:telegraphic,lang:pt]"

user: "véi, manda ver no auth aí"
capsule: "raw:imp app/auth [tone:mano,lang:pt]"

user: "Por gentileza, implemente o módulo de autenticação conforme a especificação."
capsule: "raw:imp app/auth conforme spec [tone:formal,lang:pt]"

user: "fix(auth): handle null token in middleware"
capsule: "raw:fix app/auth handle null token middleware [tone:code,lang:en]"

user: "tipo, vê um cadinho a parada do dashboard?"
capsule: "raw:check app/dash [tone:diminutivo,lang:pt]"

user: "T51 imp app/auth schema val build"
capsule: "raw:T51 imp app/auth schema val build [tone:telegraphic,lang:pt]"

user: "bro, can you implement F2 with caching?"
capsule: "raw:imp app/F2 with caching [tone:casual,lang:en]"

user: "could you please review the architecture of the auth subsystem?"
capsule: "raw:review app/auth architecture [tone:formal,lang:en]"

user: "kkk a brain comeu mosca, dá uma olhada"
capsule: "raw:audit brain misbehavior [tone:meme,lang:pt]"

user: "deploy F1 to staging — careful, prod follows next"
capsule: "raw:deploy app/F1 staging, prod next [tone:imperative,lang:en]"

user: "fala, blz? pode me dar uma força com o roteamento?"
capsule: "raw:help routing [tone:casual,lang:pt]"

user: "❤️ obrigado por sempre fazer certinho!"
capsule: "raw:ack thanks [tone:emotive,lang:pt]"

user: "EXCEPTION at line 42: NullPointerException in AuthHandler.validate()"
capsule: "raw:debug NPE app/auth/AuthHandler.validate L42 [tone:bug_report,lang:en]"

user: "olha só, tava pensando — e se fizéssemos isso aqui em paralelo?"
capsule: "raw:propose parallelization [tone:tentative,lang:pt]"

user: "F1 tá pronto? me dá um update"
capsule: "raw:status app/F1? [tone:imperative,lang:pt]"

user: "implementa esse F2 with prompt caching aware, treta?"
capsule: "raw:imp app/F2 with prompt cache awareness [tone:mano,lang:mix]"

user: "review please: this PR introduces a new audit hook"
capsule: "raw:review PR audit hook [tone:code_review,lang:en]"

user: "ahn... será que o T44 ficou de pé mesmo?"
capsule: "raw:verify T44 status? [tone:tentative,lang:pt]"
""".strip()


def encode(
    raw_message: str,
    *,
    project_root: Path | None = None,
    model: str = DEFAULT_ENCODER_MODEL,
    client: anthropic.Anthropic | None = None,
) -> tuple[str, float]:
    """Encode PT-BR raw text into a Burnless capsule with Haiku.

    The cacheable prefix (glossary + few-shots) goes into a `system` block
    with `cache_control: ephemeral 1h`, so repeated calls within the TTL
    window pay cache-read price (0.1× input) instead of full input price.
    Break-even at N=3 encoder calls per cache lifetime — see MATH.md.

    Caveat: Anthropic enforces a minimum prefix length for caching. As of
    early 2025 this is 1024 tokens for Sonnet/Opus and 2048 tokens for
    Haiku. Current FEW_SHOTS + glossary is ~1381 tokens — below Haiku's
    2048 threshold, so on Haiku the cache_control directive may be silently
    ignored until the glossary grows. Verify in production by inspecting
    `usage.cache_creation_input_tokens` and `usage.cache_read_input_tokens`
    on the response. If both stay zero, caching isn't activating and the
    prefix needs to grow (or move encoder to Sonnet, where 1024 suffices).
    """
    try:
        compressed = minify(raw_message) or raw_message
    except Exception:
        compressed = raw_message
    cached_prefix = _build_cached_prefix(project_root=project_root)
    user_part = _build_user_part(compressed)
    try:
        client = client or anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=200,
            system=[
                {
                    "type": "text",
                    "text": cached_prefix,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            messages=[{"role": "user", "content": user_part}],
        )
    except Exception:
        return _fallback_capsule(raw_message), 0.6

    text = _response_text(response)
    capsule = _extract_capsule_text(text)
    if not capsule:
        return _fallback_capsule(raw_message), 0.6
    final_capsule = _wrap_capsule_lines(capsule)
    _record_encoder_metrics(
        project_root=project_root,
        raw_message=raw_message,
        response=response,
    )
    return final_capsule, _score_capsule(capsule)


def _record_encoder_metrics(
    *,
    project_root: Path | None,
    raw_message: str,
    response: Any,
) -> None:
    """Best-effort: log encoder metrics. Silent failure — never block encode."""
    try:
        from .. import metrics as metrics_mod
        from .. import paths as paths_mod

        if project_root is None:
            return
        # Find the .burnless root by walking up — encoder may be called from
        # various depths.
        root = project_root
        bl_root = None
        for candidate in [root, *root.parents]:
            if (candidate / ".burnless").is_dir():
                bl_root = candidate / ".burnless"
                break
            if candidate.name == ".burnless":
                bl_root = candidate
                break
        if bl_root is None:
            return
        p = paths_mod.paths_for(bl_root)
        usage = getattr(response, "usage", None)
        capsule_output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        metrics_mod.record_encoder_call(
            metrics_path=p["metrics"],
            audit_path=p["audit"],
            raw_input_chars=len(raw_message or ""),
            capsule_output_tokens=capsule_output_tokens,
        )
    except Exception:
        # Metrics is observability, never block the hot path.
        return


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


def _build_cached_prefix(project_root: Path | None = None) -> str:
    """Cacheable encoder prefix: glossary + few-shot examples.

    Byte-stable within a project, so it benefits from prompt caching.
    Goes into the `system` block with `cache_control: ephemeral 1h`.
    """
    return "\n\n".join(
        [
            "[GLOSSARY]",
            load_glossary(project_root),
            "[FEW_SHOTS]",
            FEW_SHOTS,
        ]
    )


def _build_user_part(raw_message: str) -> str:
    """Variable per-call encoder input: raw message + capsule cue."""
    return "\n\n".join(
        [
            "[USER]",
            raw_message,
            "[CAPSULE]",
        ]
    )


def _build_prompt(raw_message: str, *, project_root: Path | None = None) -> str:
    """Backward-compat: full prompt as a single string (uncached path)."""
    return _build_cached_prefix(project_root=project_root) + "\n\n" + _build_user_part(raw_message)


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
