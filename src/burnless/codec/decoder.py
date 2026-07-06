from __future__ import annotations

from pathlib import Path
from typing import Any

import anthropic

from .glossary_loader import load_glossary
from .. import config

DEFAULT_DECODER_MODEL = config.HAIKU_MODEL
DEFAULT_DENSITY = {
    "efficiency": 0.5,
    "creativity": 0.5,
    "out_of_box": 0.5,
}

_STATUS_SYNONYMS = {
    "ok": "OK",
    "complete": "OK",
    "completed": "OK",
    "done": "OK",
    "success": "OK",
    "succeeded": "OK",
    "successful": "OK",
    "finished": "OK",
    "pass": "OK",
    "passed": "OK",
    "part": "PART",
    "partial": "PART",
    "partially": "PART",
    "incomplete": "PART",
    "err": "ERR",
    "error": "ERR",
    "errored": "ERR",
    "failed": "ERR",
    "failure": "ERR",
    "fail": "ERR",
    "blk": "BLK",
    "blocked": "BLK",
    "block": "BLK",
}

STYLE_GUIDE = """
The capsule may include [tone:X,lang:Y]. Match THAT tone in your output —
this is per-this-turn, not a user profile. If voice_sample is provided,
the user's actual message gives finer signal; match that register too.
Default fallback if no tone tag: friendly + direct + 1-4 sentences.

[CAPSULE → OUTPUT — examples by tone]

capsule: "gld :: OK status app/auth d010 d011 [tone:formal,lang:en]"
output: "Authentication module status verified; both delegations completed successfully."

capsule: "gld :: OK status app/auth d010 d011 [tone:casual,lang:pt]"
output: "auth tá em ordem, d010 e d011 também."

capsule: "gld :: OK status app/auth d010 d011 [tone:mano,lang:pt]"
output: "blz mano, auth de boa, dois delegations OK."

capsule: "gld :: OK status app/auth d010 d011 [tone:telegraphic,lang:pt]"
output: "auth ok. d010 d011 ok."

capsule: "gld :: OK status app/auth d010 d011 [tone:diminutivo,lang:pt]"
output: "ó, auth tá tranquilinho — d010 e d011 também, bonitinhos."

capsule: "gld del→T51 slv imp app/auth :: schema+router+prompts [tone:imperative,lang:en]"
output: "Opening T51 to implement the auth flow and validate the build."

capsule: "gld :: PART build failed [tone:casual,lang:pt]"
output: "deu ruim na build, parte foi mas trancou. detalhe em T44."

capsule: "gld :: PART build failed [tone:formal,lang:en]"
output: "Partial progress; build failed. See exec_log T44 for detail."

capsule: "gld :: PART build failed [tone:mano,lang:pt]"
output: "véi, build trancou — fez parte e parou. olha o T44."

capsule: "gld :: need to pick app/F1 or app/F2? [tone:imperative,lang:en]"
output: "Pick: F1 or F2?"

capsule: "gld :: need to pick app/F1 or app/F2? [tone:diminutivo,lang:pt]"
output: "tem que escolher um cadinho — F1 ou F2?"

capsule: "gld :: had idea received [tone:emotive,lang:pt]"
output: "anotei a ideia ❤️ vamos elaborar."

capsule: "gld :: had idea received [tone:formal,lang:pt]"
output: "Ideia registrada. Pronto para detalhar quando desejar."

capsule: "gld :: had idea received [tone:meme,lang:pt]"
output: "salvo. kkk manda o resto."

capsule: "gld :: NPE app/auth/AuthHandler.validate L42 [tone:bug_report,lang:en]"
output: "AuthHandler.validate L42: NullPointerException. Investigating null path on token."

capsule: "gld :: review PR audit hook [tone:code_review,lang:en]"
output: "PR with audit hook is up — ready when you are."

capsule: "gld :: status app/F1 ok [tone:tentative,lang:pt]"
output: "parece que F1 tá ok, mas posso re-verificar se quiser."

capsule: "gld :: T51 OK schema+router+prompts [tone:telegraphic,lang:pt]"
output: "T51 ok. schema, router, prompts."
""".strip()


def decode(
    capsule: str,
    *,
    project_root: Path | None = None,
    model: str = DEFAULT_DECODER_MODEL,
    client: anthropic.Anthropic | None = None,
    voice_sample: str | None = None,
) -> str:
    """Convert capsule → prose. If `voice_sample` (last user raw message) is
    provided, the decoder is instructed to mirror the user's tone, slang and
    register — same content, but spoken back in *their* voice. Costs a few
    extra input tokens; massively warmer UX. Default of cmd_brain/shell is
    to pass it; pass `voice_sample=None` to skip (faster, more robotic).

    Caching: the stable prefix (preamble + base_tone + glossary + STYLE_GUIDE)
    goes in `system` with `cache_control: ephemeral 1h`. Voice sample +
    capsule are variable-per-turn and live in the user message. Same threshold
    caveat as encoder: Anthropic requires ~2048 tokens for Haiku caching;
    current prefix may be below threshold until STYLE_GUIDE/glossary grow.
    Verify in production via `usage.cache_read_input_tokens`.
    """
    capsule = (capsule or "").strip()
    if not capsule:
        return ""
    client = client or anthropic.Anthropic()
    base_tone = (
        "Tone: friendly, direct, no fluff. Respond in 1 to 4 sentences. "
        "No headings, markdown, bullets, emoji or meta-commentary. "
        "Do not explain that conversion, glossary, capsule or protocol happened."
    )

    # Cacheable prefix — byte-stable across calls within a project.
    cached_prefix = "\n\n".join(
        [
            "Convert Burnless capsules into natural prose in the user's language.",
            base_tone,
            "[GLOSSARY]",
            load_glossary(project_root),
            "[STYLE_GUIDE]",
            STYLE_GUIDE,
        ]
    )

    # Variable per-turn part — voice sample and capsule.
    user_blocks: list[str] = []
    if voice_sample and voice_sample.strip():
        sample_clip = voice_sample.strip()[:400]
        user_blocks.extend(
            [
                "[USER_VOICE_SAMPLE — match this register/slang/warmth]",
                sample_clip,
                (
                    "Match the user's writing voice from the sample above: same language, "
                    "same level of formality, same use of slang/diminutives/emoticons if any. "
                    "Keep the content faithful to [CAPSULE], but speak it back in their voice. "
                    "Do NOT echo the sample literally."
                ),
            ]
        )
    user_blocks.extend(["[CAPSULE]", capsule, "[OUTPUT]"])
    user_part = "\n\n".join(user_blocks)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=700,
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
        return capsule
    text = _response_text(response)
    _record_decoder_metrics(
        project_root=project_root,
        capsule=capsule,
        response=response,
    )
    return text or capsule


def _record_decoder_metrics(
    *,
    project_root: Path | None,
    capsule: str,
    response: Any,
) -> None:
    """Best-effort: log decoder metrics. Silent failure — never block decode."""
    try:
        from .. import metrics as metrics_mod
        from .. import paths as paths_mod

        if project_root is None:
            return
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
        expanded_output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        # Capsule input is short; estimate by chars / 3.5.
        capsule_input_tokens = max(int(len(capsule or "") / 3.5), 0)
        metrics_mod.record_decoder_call(
            metrics_path=p["metrics"],
            audit_path=p["audit"],
            capsule_input_tokens=capsule_input_tokens,
            expanded_output_tokens=expanded_output_tokens,
        )
    except Exception:
        return


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


_LIST_FIELDS = ("validated", "evidence", "files_touched", "issues")


def _coerce_to_list(value: Any) -> list:
    """Tolerate workers that emit bool/str/dict where a list was expected.
    Workers occasionally return `validated: true` or `files_touched: "a.py"`
    instead of lists; this keeps downstream call sites crash-free."""
    if value is None or value is False:
        return []
    if value is True:
        return ["true"]
    if isinstance(value, list):
        return value
    if isinstance(value, (str, int, float)):
        return [value]
    if isinstance(value, dict):
        return [f"{k}={v}" for k, v in value.items()]
    return [str(value)]


def _coerce_path_entry(entry: Any) -> str | None:
    """Reduce one files_touched entry to a plain path string.

    Workers may report files_touched as strings or as dicts like
    ``{"path": "a.py", "lines": "1-9"}``. Downstream consumers join these onto
    cwd, py_compile them, and put them in a set() for indexing — all of which
    need hashable, path-like strings. A dict entry would crash those call sites
    (isabs() / unhashable set member), so extract the path or drop the entry.
    """
    if isinstance(entry, str):
        return entry.strip() or None
    if isinstance(entry, dict):
        for key in ("path", "file", "filename", "name", "target"):
            val = entry.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return None
    if entry is None:
        return None
    return str(entry)


def normalize_worker_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Backwards-compatible normalization for worker JSON envelopes."""
    normalized = dict(payload or {})

    # Canonicalize status field to ensure verify gate and exit code logic work correctly
    if "status" in normalized:
        raw_status = str(normalized.get("status") or "").strip()
        if raw_status:
            lower_status = raw_status.lower()
            if lower_status in _STATUS_SYNONYMS:
                normalized["status"] = _STATUS_SYNONYMS[lower_status]
            elif raw_status.upper() in {"OK", "PART", "ERR", "BLK"}:
                normalized["status"] = raw_status.upper()

    raw_density = normalized.get("density")
    density = raw_density if isinstance(raw_density, dict) else {}
    normalized["density"] = {
        key: _clip_unit_float(density.get(key), default=default)
        for key, default in DEFAULT_DENSITY.items()
    }
    normalized["salience"] = _clip_unit_float(normalized.get("salience"), default=0.5)
    for field in _LIST_FIELDS:
        if field in normalized:
            normalized[field] = _coerce_to_list(normalized[field])
    # files_touched must be a list of path strings: element-coerce dict entries
    # (e.g. {"path": ...}) so the syntax gate, set-based indexing and reports
    # never choke on a non-string / unhashable member.
    if "files_touched" in normalized:
        normalized["files_touched"] = [
            p
            for p in (_coerce_path_entry(e) for e in normalized["files_touched"])
            if p
        ]
    return normalized


def _clip_unit_float(value: Any, *, default: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if num < 0.0:
        return 0.0
    if num > 1.0:
        return 1.0
    return num
