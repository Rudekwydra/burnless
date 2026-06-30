from __future__ import annotations
import hashlib
import json
import os
import re
import shlex
import subprocess
import shutil
import sys
from pathlib import Path
from datetime import datetime, timezone

from . import config, pricing


class AgentError(RuntimeError):
    pass


_VALID_SANDBOX = {"read-only", "workspace-write", "danger-full-access"}
_DECISIONS_CACHE_ENV = "BURNLESS_DECISIONS_CACHE_PATH"
_PROVIDER_HEALTH_ENV = "BURNLESS_PROVIDER_HEALTH_PATH"
_SIMILARITY_THRESHOLD = 0.7
_MAX_CONTEXT_SUMMARY_CHARS = 280
_MAX_DECISION_TEXT_CHARS = 280
_MIN_TOKEN_LEN = 3
_TOKEN_RE = re.compile(r"[a-z0-9_./+-]+", re.IGNORECASE)
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_RETRYABLE_PROVIDER_ERROR_RE = re.compile(r"\b(?:5\d\d|timeout|timed out)\b", re.IGNORECASE)
_SUPPORTED_PROVIDER_IDS = {"anthropic", "openai", "openrouter", "gemini", "ollama-local"}
_DECISION_PATTERNS = (
    re.compile(r"\b(?:DECISION|DECIDED|DECIDIMOS|DECIS[AÃ]O)\s*:\s*([^\n]{8,280})", re.IGNORECASE),
    re.compile(r"\b(?:choose|chose|prefer|preferred|use|using|adicionar|adicione|add)\b[^\n]{8,280}", re.IGNORECASE),
)
_ARCHITECTURE_HINTS = (
    "sqlite", "json", "tauri", "command", "migration", "schema", "database",
    "storage", "persist", "cache", "queue", "api", "endpoint", "protocol",
    "router", "service", "adapter", "model", "command x", "decision", "arquitet",
)
_STOP_TOKENS = {
    "the", "and", "for", "with", "from", "that", "this", "para", "com", "uma",
    "umas", "uns", "about", "into", "over", "under", "worker", "silver",
    "delegation", "delegacao", "burnless", "task", "goal", "report", "kind",
    "output", "final", "json", "block", "schema", "required", "success",
}

_AB_SUCCESS_W = 0.4
_AB_LATENCY_W = 0.2
_AB_COST_W = 0.3
_AB_ERROR_PENALTY = 0.4
_AB_HEADROOM_COOLDOWN_S = 300


def _parse_worker_usage(stdout: str) -> dict:
    """Best-effort extract the worker's cumulative usage from --output-format json or stream-json."""
    if not stdout:
        return {}
    # plain json: a single object with "usage"
    try:
        obj = json.loads(stdout)
        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
            return obj["usage"]
    except Exception:
        pass
    # stream-json: last line whose event is type=="result" with a usage dict
    last = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result" and isinstance(ev.get("usage"), dict):
            last = ev["usage"]
    return last


def _decisions_cache_path() -> Path:
    override = os.environ.get(_DECISIONS_CACHE_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".burnless" / "decisions_cache.json"


def _provider_health_path() -> Path:
    override = os.environ.get(_PROVIDER_HEALTH_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".burnless" / "provider_health.json"


def _load_provider_health() -> dict[str, dict]:
    path = _provider_health_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    nested = raw.get("health_scores")
    if isinstance(nested, dict):
        return nested
    return raw


def _provider_health_state() -> dict:
    path = _provider_health_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {"health_scores": {}, "last_used_provider": None}
    except Exception:
        return {"health_scores": {}, "last_used_provider": None}
    if not isinstance(raw, dict):
        return {"health_scores": {}, "last_used_provider": None}
    if isinstance(raw.get("health_scores"), dict):
        return {
            "health_scores": raw.get("health_scores") or {},
            "last_used_provider": raw.get("last_used_provider"),
        }
    return {"health_scores": raw, "last_used_provider": None}


def _save_provider_health(data: dict[str, dict], *, last_used_provider: dict | None = None) -> None:
    path = _provider_health_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "health_scores": data,
            "last_used_provider": last_used_provider,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        return


def reset_provider_health() -> int:
    path = _provider_health_path()
    current = _load_provider_health()
    try:
        if path.exists():
            path.unlink()
    except Exception:
        return 0
    return len(current)


def _provider_id_from_cfg(agent_cfg: dict) -> str:
    raw = str(agent_cfg.get("provider") or agent_cfg.get("name") or "provider").strip().lower()
    return raw or "provider"


def _health_key(tier: str, provider_cfg: dict) -> str:
    return f"{tier}:{_provider_id_from_cfg(provider_cfg)}"


def _default_health_entry(tier: str, provider_cfg: dict) -> dict:
    return {
        "tier": tier,
        "provider": _provider_id_from_cfg(provider_cfg),
        "name": str(provider_cfg.get("name") or ""),
        "command": str(provider_cfg.get("command") or ""),
        "attempts": 0,
        "successes": 0,
        "failures": 0,
        "success_rate": 1.0,
        "avg_latency": 1.0,
        "last_error_at": None,
        "last_latency_s": None,
        "updated_at": None,
    }


def _copy_agent_cfg(agent_cfg: dict) -> dict:
    out = dict(agent_cfg)
    out.pop("providers", None)
    return out


def providers_for_tier(agent_cfg: dict, *, tier: str) -> list[dict]:
    providers = agent_cfg.get("providers")
    if isinstance(providers, list):
        out: list[dict] = []
        for idx, item in enumerate(providers):
            if not isinstance(item, dict):
                continue
            merged = _copy_agent_cfg(agent_cfg)
            merged.update(item)
            merged["_provider_rank"] = idx
            out.append(merged)
        if out:
            return out
    fallback = _copy_agent_cfg(agent_cfg)
    fallback.setdefault("provider", _provider_id_from_cfg(agent_cfg))
    fallback["_provider_rank"] = 0
    return [fallback]


def rank_providers(agent_cfg: dict, *, tier: str) -> list[dict]:
    providers = providers_for_tier(agent_cfg, tier=tier)
    health = _load_provider_health()
    latencies: list[float] = []
    enriched: list[dict] = []
    for provider_cfg in providers:
        key = _health_key(tier, provider_cfg)
        entry = dict(_default_health_entry(tier, provider_cfg))
        stored = health.get(key)
        if isinstance(stored, dict):
            entry.update(stored)
        latency = float(entry.get("avg_latency") or 1.0)
        latencies.append(max(latency, 0.001))
        enriched.append({"cfg": provider_cfg, "health": entry, "key": key})
    min_latency = min(latencies) if latencies else 1.0
    costs = [pricing.blended_cost(item["cfg"].get("model") or item["cfg"].get("name") or "") for item in enriched]
    positive_costs = [c for c in costs if c > 0]
    min_cost = min(positive_costs) if positive_costs else 1.0
    for item, cost in zip(enriched, costs):
        latency = max(float(item["health"].get("avg_latency") or 1.0), 0.001)
        avg_latency_norm = max(latency / min_latency, 1.0)
        success_rate = float(item["health"].get("success_rate") or 0.0)
        cost_score = 1.0 if cost <= 0 else min(min_cost / cost, 1.0)
        score = success_rate * _AB_SUCCESS_W + (1.0 / avg_latency_norm) * _AB_LATENCY_W + cost_score * _AB_COST_W
        try:
            error_at = item["health"].get("last_error_at")
            if error_at:
                error_ts = datetime.fromisoformat(error_at)
                if error_ts.tzinfo is None:
                    error_ts = error_ts.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if (now - error_ts).total_seconds() <= _AB_HEADROOM_COOLDOWN_S:
                    score *= _AB_ERROR_PENALTY
        except Exception:
            pass
        item["score"] = score
        item["avg_latency_norm"] = avg_latency_norm
    enriched.sort(
        key=lambda item: (
            -float(item["score"]),
            -float(item["health"].get("success_rate") or 0.0),
            float(item["health"].get("avg_latency") or 1.0),
            int(item["cfg"].get("_provider_rank") or 0),
        )
    )
    return enriched


def select_provider(agent_cfg: dict, *, tier: str) -> dict:
    ranked = rank_providers(agent_cfg, tier=tier)
    return ranked[0] if ranked else {"cfg": _copy_agent_cfg(agent_cfg), "health": {}, "key": "", "score": 0.0}


def provider_health_snapshot() -> dict:
    state = _provider_health_state()
    return {
        "health_scores": state.get("health_scores") or {},
        "last_used_provider": state.get("last_used_provider"),
    }


def list_provider_stats() -> list[dict]:
    rows: list[dict] = []
    for key, value in sorted(_load_provider_health().items()):
        if not isinstance(value, dict):
            continue
        row = dict(value)
        row["key"] = key
        rows.append(row)
    return rows


def record_provider_result(*, tier: str, provider_cfg: dict, success: bool, latency_s: float, error_at: str | None = None) -> dict:
    state = _provider_health_state()
    health = state.get("health_scores") or {}
    key = _health_key(tier, provider_cfg)
    entry = dict(_default_health_entry(tier, provider_cfg))
    stored = health.get(key)
    if isinstance(stored, dict):
        entry.update(stored)
    attempts = int(entry.get("attempts") or 0) + 1
    successes = int(entry.get("successes") or 0) + (1 if success else 0)
    failures = int(entry.get("failures") or 0) + (0 if success else 1)
    prev_attempts = max(attempts - 1, 0)
    prev_avg = float(entry.get("avg_latency") or latency_s or 1.0)
    entry["attempts"] = attempts
    entry["successes"] = successes
    entry["failures"] = failures
    entry["success_rate"] = successes / attempts if attempts else 1.0
    entry["avg_latency"] = latency_s if prev_attempts == 0 else ((prev_avg * prev_attempts) + latency_s) / attempts
    entry["last_latency_s"] = latency_s
    entry["updated_at"] = _now_iso()
    entry["tier"] = tier
    entry["provider"] = _provider_id_from_cfg(provider_cfg)
    entry["name"] = str(provider_cfg.get("name") or "")
    entry["command"] = str(provider_cfg.get("command") or "")
    if not success:
        entry["last_error_at"] = error_at or entry["updated_at"]
    health[key] = entry
    last_used_provider = {
        "tier": tier,
        "provider": _provider_id_from_cfg(provider_cfg),
        "name": str(provider_cfg.get("name") or ""),
        "command": str(provider_cfg.get("command") or ""),
        "updated_at": entry["updated_at"],
    }
    _save_provider_health(health, last_used_provider=last_used_provider)
    return entry


def _retryable_provider_failure(result: dict) -> bool:
    if bool(result.get("timed_out")) or bool(result.get("stale")):
        return True
    blob = "\n".join(
        str(result.get(field) or "")
        for field in ("stderr", "stdout", "error")
    )
    return bool(_RETRYABLE_PROVIDER_ERROR_RE.search(blob))


def _load_decisions_cache() -> list[dict]:
    path = _decisions_cache_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not all(k in item for k in ("decision_hash", "context_summary", "decision_text")):
            continue
        out.append(
            {
                "decision_hash": str(item.get("decision_hash") or ""),
                "context_summary": str(item.get("context_summary") or ""),
                "decision_text": str(item.get("decision_text") or ""),
                "hits": int(item.get("hits") or 0),
                "last_used": str(item.get("last_used") or ""),
            }
        )
    return out


def _save_decisions_cache(entries: list[dict]) -> None:
    path = _decisions_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        return


def list_decisions() -> list[dict]:
    return _load_decisions_cache()


def clear_decisions() -> int:
    path = _decisions_cache_path()
    existing = len(_load_decisions_cache())
    try:
        if path.exists():
            path.unlink()
    except Exception:
        return 0
    return existing


def _normalize_space(text: str, *, limit: int | None = None) -> str:
    cleaned = " ".join(str(text or "").split())
    if limit is not None and len(cleaned) > limit:
        return cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def _section_text(markdown: str, heading: str) -> str:
    matches = list(_SECTION_RE.finditer(markdown or ""))
    heading_lower = heading.strip().lower()
    for i, match in enumerate(matches):
        if match.group(1).strip().lower() != heading_lower:
            continue
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        return markdown[start:end].strip()
    return ""


def _summarize_context(prompt_or_delegation: str) -> str:
    goal = _section_text(prompt_or_delegation, "Goal")
    task = _section_text(prompt_or_delegation, "Task")
    if not goal and not task:
        goal = prompt_or_delegation
    combined = " | ".join(x for x in (goal, task) if x)
    return _normalize_space(combined, limit=_MAX_CONTEXT_SUMMARY_CHARS)


def _tokenize_for_similarity(text: str) -> set[str]:
    tokens = {
        tok.lower()
        for tok in _TOKEN_RE.findall(str(text or "").lower())
        if len(tok) >= _MIN_TOKEN_LEN and tok.lower() not in _STOP_TOKENS
    }
    return tokens


def _token_overlap_score(left: str, right: str) -> float:
    lt = _tokenize_for_similarity(left)
    rt = _tokenize_for_similarity(right)
    if not lt or not rt:
        return 0.0
    return len(lt & rt) / max(len(lt), len(rt))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decision_hash(decision_text: str) -> str:
    normalized = _normalize_space(decision_text, limit=_MAX_DECISION_TEXT_CHARS).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _extract_decision_text(summary: dict, stdout: str) -> str:
    candidates: list[str] = []
    if isinstance(summary, dict):
        for key in ("summary", "next"):
            value = summary.get(key)
            if value:
                candidates.append(str(value))
        for item in summary.get("issues") or []:
            candidates.append(str(item))
    for text in candidates:
        if any(hint in text.lower() for hint in _ARCHITECTURE_HINTS):
            return _normalize_space(text, limit=_MAX_DECISION_TEXT_CHARS)
    for pattern in _DECISION_PATTERNS:
        match = pattern.search(stdout or "")
        if match:
            piece = match.group(1) if match.groups() else match.group(0)
            if any(hint in piece.lower() for hint in _ARCHITECTURE_HINTS):
                return _normalize_space(piece, limit=_MAX_DECISION_TEXT_CHARS)
    return ""


def maybe_prepend_prior_decision(prompt: str, *, tier: str) -> str:
    if tier != "silver":
        return prompt
    context_summary = _summarize_context(prompt)
    if not context_summary:
        return prompt
    entries = _load_decisions_cache()
    best: dict | None = None
    best_score = 0.0
    for entry in entries:
        score = _token_overlap_score(context_summary, entry.get("context_summary", ""))
        if score > _SIMILARITY_THRESHOLD and score > best_score:
            best = entry
            best_score = score
    if best is None:
        return prompt
    best["hits"] = int(best.get("hits") or 0) + 1
    best["last_used"] = _now_iso()
    _save_decisions_cache(entries)
    prior_block = (
        "## PRIOR DECISION\n\n"
        f"- context_summary: {best.get('context_summary', '')}\n"
        f"- decision_text: {best.get('decision_text', '')}\n"
        f"- cache_match_score: {best_score:.2f}\n"
        "- Reuse this prior architectural decision unless the current repo state proves it wrong.\n\n"
    )
    return prior_block + prompt.lstrip()


def remember_silver_decision(*, tier: str, prompt: str, summary: dict, stdout: str) -> dict | None:
    if tier != "silver":
        return None
    context_summary = _summarize_context(prompt)
    decision_text = _extract_decision_text(summary, stdout)
    if not context_summary or not decision_text:
        return None
    entry = {
        "decision_hash": _decision_hash(decision_text),
        "context_summary": context_summary,
        "decision_text": decision_text,
        "hits": 1,
        "last_used": _now_iso(),
    }
    entries = _load_decisions_cache()
    for existing in entries:
        if existing.get("decision_hash") == entry["decision_hash"]:
            existing["context_summary"] = entry["context_summary"]
            existing["decision_text"] = entry["decision_text"]
            existing["hits"] = max(int(existing.get("hits") or 0), 0) + 1
            existing["last_used"] = entry["last_used"]
            _save_decisions_cache(entries)
            return existing
    entries.append(entry)
    _save_decisions_cache(entries)
    return entry


def _strip_flag(parts: list[str], flag: str) -> list[str]:
    out: list[str] = []
    skip_next = False
    for token in parts:
        if skip_next:
            skip_next = False
            continue
        if token == flag:
            skip_next = True
            continue
        if token.startswith(flag + "="):
            continue
        out.append(token)
    return out


def _apply_codex_overrides(parts: list[str], agent_cfg: dict) -> list[str]:
    """Apply optional declarative overrides for codex-backed tiers."""
    sandbox = agent_cfg.get("sandbox") or os.environ.get("BURNLESS_SANDBOX")
    workspace_root = agent_cfg.get("workspace_root") or os.environ.get("BURNLESS_WORKSPACE_ROOT")
    allow_net = agent_cfg.get("allow_net") or os.environ.get("BURNLESS_ALLOW_NET") in ("1", "true", "yes")

    if not (sandbox or workspace_root or allow_net):
        return parts
    if not parts or parts[0] != "codex":
        return parts

    out = list(parts)
    if sandbox:
        if sandbox not in _VALID_SANDBOX:
            raise AgentError(
                f"invalid agent sandbox={sandbox!r}; expected one of {sorted(_VALID_SANDBOX)}"
            )
        out = _strip_flag(out, "--sandbox")
        try:
            insert_at = out.index("exec") + 1
        except ValueError:
            insert_at = 1
        out[insert_at:insert_at] = ["--sandbox", sandbox]
    if allow_net and not agent_cfg.get("sandbox"):
        out = _strip_flag(out, "--sandbox")
        try:
            insert_at = out.index("exec") + 1
        except ValueError:
            insert_at = 1
        out[insert_at:insert_at] = ["--full-auto"]
    if workspace_root:
        root_path = str(Path(workspace_root).expanduser())
        out = _strip_flag(out, "--cd")
        out += ["--cd", root_path]
    return out


def resolve_command(agent_cfg: dict) -> list[str]:
    cmd = agent_cfg.get("command", "").strip()
    if not cmd:
        raise AgentError(f"agent missing 'command': {agent_cfg}")
    parts = shlex.split(cmd)
    parts = _apply_codex_overrides(parts, agent_cfg)
    parts = _dedup_valueless_flags(parts)
    return parts


def is_available(agent_cfg: dict) -> bool:
    from .ollama_worker import is_ollama_tools_agent
    if is_ollama_tools_agent(agent_cfg):
        return True
    parts = resolve_command(agent_cfg)
    return shutil.which(parts[0]) is not None


def _extract_model_from_parts(parts: list[str]) -> str | None:
    """Look for `--model X` or `-m X` in CLI args (claude/codex pattern).

    Returns the canonical model id (alias resolved) or None if not found.
    """
    for i, tok in enumerate(parts):
        if tok in ("--model", "-m") and i + 1 < len(parts):
            return config.normalize_model(parts[i + 1])
    return None


def _detect_provider_from_parts(parts: list[str]) -> str | None:
    """Inspect parts[0] (or the first non-flag token) to identify provider.

    Returns 'claude' for claude CLI invocations, 'codex' for codex CLI,
    and None for anything else (gemini, ollama, openrouter — structurally
    no warm-fork support).
    """
    if not parts:
        return None
    bin_name = Path(parts[0]).name.lower()
    if "claude" in bin_name:
        return "claude"
    if "codex" in bin_name:
        return "codex"
    return None


def _dedup_valueless_flags(parts: list[str]) -> list[str]:
    """Remove duplicate valueless flags, preserving first occurrence.

    Deduplicates: --skip-git-repo-check, --ignore-user-config, --ignore-rules, --full-auto.
    Value-taking flags (--cd, --sandbox, --model, -m) and their values are preserved as-is.
    """
    valueless_flags = {"--skip-git-repo-check", "--ignore-user-config", "--ignore-rules", "--full-auto"}
    seen_valueless = set()
    result = []
    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok in valueless_flags:
            if tok not in seen_valueless:
                seen_valueless.add(tok)
                result.append(tok)
            i += 1
        else:
            result.append(tok)
            i += 1
    return result


def _inject_warm_fork_args(parts: list[str], cwd: Path | None) -> tuple[list[str], str, str | None]:
    """Inject warm CLI args and return (parts, warm_prefix, iso_cwd) for a worker command.

    Detects (provider, model) from the command parts. If provider has warm
    support (claude/codex), loads the per-model warm pool via the unified
    warm protocol (warm_args + warm_prefix), auto-initializes if missing/expired.
    If provider has no warm support (gemini, ollama), returns (parts, "", None) silently.

    Per [[rule-worker-never-fresh-2026-05-24]] + per-model refactor.
    """
    if cwd is None:
        return parts, "", None
    provider = _detect_provider_from_parts(parts)
    if provider is None:
        return parts, "", None  # structurally no warm support — silent skip
    model = _extract_model_from_parts(parts)
    if model is None:
        # Default by provider
        model = config.DEFAULT_PROVIDER_MODELS.get(provider, config.DEFAULT_PROVIDER_MODELS["claude"])
    burnless_root = Path(cwd) / ".burnless"
    try:
        # Single-source: the warm pool follows from the provider's cache mode
        # (cache_modes registry), not a hardcoded provider branch. warm_module is
        # auth-invariant today, so "subscription" is a safe resolution key.
        import importlib
        from .coreconfig.schema import Agent as _Agent
        from .coreconfig.resolver import resolve_cache_mode as _resolve_cache_mode
        _prov = "anthropic" if provider == "claude" else provider  # detect-vocab → Agent-vocab
        _cmode = _resolve_cache_mode(_Agent(name="_warm", role="execute", provider=_prov, auth="subscription"))
        if not _cmode.warm_module:
            return parts, "", None  # structural cold — silent (gemini/ollama)
        _ws = importlib.import_module(_cmode.warm_module)
        fn = getattr(_ws, "warm_args", None) or getattr(_ws, "fork_args", None)
        extra = fn(burnless_root, model)
        if not extra:
            try:
                _ws.init(burnless_root, model=model)
                extra = fn(burnless_root, model)
            except Exception as _init_e:
                print(
                    f"[burnless] WARN: warm pool auto-init failed for "
                    f"{provider}/{model} ({_init_e}); worker will spawn COLD.",
                    file=sys.stderr, flush=True,
                )
                extra = []
    except Exception as _ws_e:
        print(
            f"[burnless] WARN: warm_session module unavailable ({_ws_e}); "
            f"worker will spawn COLD.",
            file=sys.stderr, flush=True,
        )
        return parts, "", None
    if not extra:
        print(
            f"[burnless] WARN: no warm fork args available for {provider}/{model} "
            f"after init; worker spawning COLD.",
            file=sys.stderr, flush=True,
        )
        return parts, "", None
    prefix_fn = getattr(_ws, "warm_prefix", None)
    warm_prefix = prefix_fn(burnless_root, model) if prefix_fn else ""
    # Strip --append-system-prompt <text> pair to keep prefix byte-stable
    # with the warm session (logic identical to pre-refactor).
    stripped: list[str] = []
    skip_next = False
    for tok in parts:
        if skip_next:
            skip_next = False
            continue
        if tok == "--append-system-prompt":
            skip_next = True
            continue
        stripped.append(tok)
    iso_cwd = None
    try:
        iso_cwd = _ws.worker_cwd(burnless_root, model)
    except Exception:
        iso_cwd = None
    return _dedup_valueless_flags(stripped + list(extra)), warm_prefix, iso_cwd


def _run_once(agent_cfg: dict, prompt: str, *, timeout: int = 600, cwd: Path | None = None) -> dict:
    from .ollama_worker import is_ollama_tools_agent, run_ollama_tools
    if is_ollama_tools_agent(agent_cfg):
        model = agent_cfg.get("model") or agent_cfg.get("name") or "llama3.1"
        sysp = ""
        env = run_ollama_tools(
            model, prompt, cwd=str(cwd) if cwd else None, system_prompt=sysp, timeout=timeout,
            shell_timeout=int(agent_cfg.get("shell_timeout_s", 600))
        )
        import json as _json
        rc = 0 if env.get("status") == "OK" else 1
        return {
            "agent": agent_cfg.get("name"),
            "provider": _provider_id_from_cfg(agent_cfg),
            "command": [],
            "stdout": _json.dumps(env, ensure_ascii=False),
            "stderr": "",
            "returncode": rc,
            "started_at": "",
            "ended_at": "",
            "duration_s": 0.0,
            "timed_out": False,
            "usage": {},
        }
    parts = resolve_command(agent_cfg)
    parts, warm_prefix, iso_cwd = _inject_warm_fork_args(parts, cwd)
    if warm_prefix:
        prompt = warm_prefix + "\n\n" + prompt
    run_cwd = iso_cwd or (str(cwd) if cwd else None)
    if shutil.which(parts[0]) is None:
        raise AgentError(
            f"agent binary not found in PATH: {parts[0]} (configured for {agent_cfg.get('name')})"
        )
    started = datetime.now(timezone.utc)
    worker_env = os.environ.copy()
    worker_env["BURNLESS_WORKER"] = "1"
    # Force `claude -p` (and any tier subprocess) to authenticate via Claude
    # Code OAuth/subscription instead of falling through to API billing. The
    # in-process SDK paths still read the key directly from ANTHROPIC_ENV_PATHS.
    worker_env.pop("ANTHROPIC_API_KEY", None)
    try:
        proc = subprocess.run(
            parts,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=run_cwd,
            env=worker_env,
        )
    except subprocess.TimeoutExpired as e:
        ended = datetime.now(timezone.utc)
        return {
            "agent": agent_cfg.get("name"),
            "provider": _provider_id_from_cfg(agent_cfg),
            "command": parts,
            "stdout": e.stdout or "",
            "stderr": e.stderr or "",
            "error": f"agent {agent_cfg.get('name')} timed out after {timeout}s",
            "returncode": 124,
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "duration_s": (ended - started).total_seconds(),
            "timed_out": True,
        }
    ended = datetime.now(timezone.utc)
    if cwd is not None:
        try:
            _prov = _detect_provider_from_parts(list(parts))
            _model = _extract_model_from_parts(list(parts))
            if _model is None:
                _model = config.DEFAULT_PROVIDER_MODELS.get(_prov, config.DEFAULT_PROVIDER_MODELS["claude"])
            if _prov == "claude":
                from . import warm_session as _ws
                _ws.touch(Path(cwd) / ".burnless", _model)
            elif _prov == "codex":
                # warm_session_codex has no touch() (uses refresh-mtime semantics)
                pass
        except Exception:
            pass
    return {
        "agent": agent_cfg.get("name"),
        "provider": _provider_id_from_cfg(agent_cfg),
        "command": parts,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_s": (ended - started).total_seconds(),
        "usage": _parse_worker_usage(proc.stdout),
    }


def run(agent_cfg: dict, prompt: str, *, timeout: int = 600, cwd: Path | None = None, tier: str | None = None) -> dict:
    """Execute the agent CLI with provider autobalance + retryable fallback."""
    tier_name = str(tier or agent_cfg.get("provider_tier") or "default")
    ranked = rank_providers(agent_cfg, tier=tier_name)
    if not ranked:
        return _run_once(agent_cfg, prompt, timeout=timeout, cwd=cwd)
    attempts: list[dict] = []
    for idx, item in enumerate(ranked):
        provider_cfg = item["cfg"]
        result = _run_once(provider_cfg, prompt, timeout=timeout, cwd=cwd)
        success = int(result.get("returncode") or 0) == 0 and not bool(result.get("timed_out"))
        record_provider_result(
            tier=tier_name,
            provider_cfg=provider_cfg,
            success=success,
            latency_s=float(result.get("duration_s") or 0.0),
            error_at=_now_iso() if not success else None,
        )
        result["selected_provider"] = _provider_id_from_cfg(provider_cfg)
        attempts.append(
            {
                "provider": result["selected_provider"],
                "returncode": result.get("returncode"),
                "timed_out": bool(result.get("timed_out")),
            }
        )
        result["provider_attempts"] = attempts
        if success:
            return result
        if idx < len(ranked) - 1 and not _retryable_provider_failure(result):
            return result
    result["provider_attempts"] = attempts
    return result


class AutobalanceWorker:
    """Provider autobalance helper with persisted health scores."""

    def __init__(self, agent_cfg: dict, *, tier: str):
        self.agent_cfg = agent_cfg
        self.tier = tier

    @property
    def health_scores(self) -> dict[str, dict]:
        return provider_health_snapshot()["health_scores"]

    @property
    def last_used_provider(self) -> dict | None:
        return provider_health_snapshot()["last_used_provider"]

    def rank_providers(self) -> list[dict]:
        return rank_providers(self.agent_cfg, tier=self.tier)

    def run(self, prompt: str, *, timeout: int = 600, cwd: Path | None = None) -> dict:
        return run(self.agent_cfg, prompt, timeout=timeout, cwd=cwd, tier=self.tier)
