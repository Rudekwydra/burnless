"""Debugless: GoPro-trace analyser for burnless delegations.

Reads .burnless/delegations/<did>.md + .burnless/logs/<did>.log,
calls local ollama to identify vestigials/loops/dead branches/ghost refs,
and writes a capsule to .burnless/debugless/.

Invoked manually via `burnless trace <did>` or `burnless debugless sweep`.
All external dependencies: stdlib + burnless.config + ollama HTTP API.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import config

_LOG_TAIL_CHARS = 8000
_DEFAULT_MODEL = config.DEFAULT_LOCAL_MODEL


def _read_delegation(burnless_root: Path, did: str) -> tuple[str, str]:
    """Read .burnless/delegations/<did>.md (spec) + .burnless/logs/<did>.log (worker output).
       Truncate log to last 8000 chars if longer (keep tail).
       Returns (spec_text, log_text). Raise FileNotFoundError if missing."""
    spec_path = burnless_root / "delegations" / f"{did}.md"
    log_path = burnless_root / "logs" / f"{did}.log"
    if not spec_path.exists():
        raise FileNotFoundError(f"delegation spec not found: {spec_path}")
    if not log_path.exists():
        raise FileNotFoundError(f"delegation log not found: {log_path}")
    spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
    log_raw = log_path.read_text(encoding="utf-8", errors="replace")
    log_text = log_raw[-_LOG_TAIL_CHARS:] if len(log_raw) > _LOG_TAIL_CHARS else log_raw
    return spec_text, log_text


def _build_prompt(spec_text: str, log_text: str, did: str) -> str:
    """Build the gopro-trace prompt for ollama. See PROMPT TEMPLATE section below."""
    return (
        f"You are Debugless: a code path tracer for burnless delegations.\n\n"
        f"Below is a delegation spec and its worker execution log.\n\n"
        f"=== DELEGATION SPEC (did={did}) ===\n"
        f"{spec_text}\n\n"
        f"=== WORKER LOG (last 8000 chars) ===\n"
        f"{log_text}\n\n"
        f"Walk through this delegation step-by-step as if wearing a GoPro on your head, "
        f"observing each step from the token's perspective.\n\n"
        f"Then identify:\n"
        f"- VESTIGIAL: code paths that only run as fallback for missing newer infrastructure\n"
        f"- LOOPS: patterns that revisit the same state/file/UUID multiple times\n"
        f"- DEAD: branches whose condition is always False given the project state\n"
        f"- GHOST: references to state files / UUIDs / sessions that don't exist on disk\n\n"
        f"Return ONLY a JSON object (no preamble, no markdown fences) with this exact schema:\n"
        f"{{\n"
        f'  "path": ["step 1: ...", "step 2: ...", "..."],\n'
        f'  "vestigials": ["..."],\n'
        f'  "loops": ["..."],\n'
        f'  "dead_branches": ["..."],\n'
        f'  "ghost_refs": ["..."],\n'
        f'  "recommendation": "one-sentence main finding"\n'
        f"}}\n\n"
        f"If you find nothing in a category, use an empty list. Keep `path` under 20 items."
    )


def _call_ollama(prompt: str, model: str, timeout: int) -> tuple[str, int]:
    """Invoke ollama via HTTP API POST /api/generate (stream=false, format=json).
       Uses the HTTP API rather than the `ollama run` CLI because the CLI emits
       TTY re-render artifacts that corrupt JSON even with TERM=dumb/NO_COLOR.
       Returns (response_text, exit_code): 0 ok, -1 timeout, -2 unreachable, -3 bad response."""
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").strip()
    if not host.startswith("http"):
        host = "http://" + host
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }).encode("utf-8")
    req = urllib.request.Request(
        host.rstrip("/") + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        # Same gemma-4 post-processing as the tuned epoch path: strip harmony
        # <channel|> thought tokens + residual ANSI (epochs._ollama does this).
        from .compression import _strip_gemma_channels
        return _strip_gemma_channels(body.get("response", "")), 0
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), TimeoutError):
            return "", -1
        return "", -2
    except TimeoutError:
        return "", -1
    except (ValueError, OSError):
        return "", -3


def _parse_ollama_json(raw: str) -> dict:
    """Try to extract JSON from raw ollama output (may include preamble/markdown).
       Strategy: find first '{' and matching '}'. json.loads it.
       On parse failure: return {'_parse_error': True, '_raw_excerpt': raw[:500]}."""
    # Defensive cleanup: strip ANSI/CSI escape codes if subprocess captured TTY output
    raw = re.sub(r'\x1b?\[[?0-9;]*[a-zA-Z]', '', raw)
    raw = re.sub(r'\x1b\][^\x07]*\x07', '', raw)
    # Strip markdown code fences if present
    raw = raw.replace('```json', '').replace('```', '')
    start = raw.find("{")
    if start == -1:
        return {"_parse_error": True, "_raw_excerpt": raw[:500]}
    depth = 0
    end = -1
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return {"_parse_error": True, "_raw_excerpt": raw[:500]}
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {"_parse_error": True, "_raw_excerpt": raw[:500]}


def trace(burnless_root: Path, did: str, *, model: str = _DEFAULT_MODEL, timeout: int = 90) -> dict:
    """Trace a single delegation. Returns dict with keys:
       did, model_used, path (list[str]), vestigials (list[str]), loops (list[str]),
       dead_branches (list[str]), ghost_refs (list[str]), recommendation (str),
       ok (bool), error (str | None).
       Calls ollama via HTTP API (POST /api/generate, format=json)."""
    base: dict = {
        "did": did,
        "model_used": model,
        "path": [],
        "vestigials": [],
        "loops": [],
        "dead_branches": [],
        "ghost_refs": [],
        "recommendation": "",
        "ok": False,
        "error": None,
    }
    try:
        spec_text, log_text = _read_delegation(burnless_root, did)
    except FileNotFoundError as e:
        base["error"] = str(e)
        return base

    prompt = _build_prompt(spec_text, log_text, did)
    raw, exit_code = _call_ollama(prompt, model, timeout)

    if exit_code == -1:
        base["error"] = f"ollama timeout after {timeout}s"
        return base
    if exit_code == -2:
        base["error"] = "ollama not reachable (is the server running?)"
        return base
    if exit_code != 0:
        base["error"] = f"ollama exited {exit_code}"
        return base

    parsed = _parse_ollama_json(raw)
    if parsed.get("_parse_error"):
        base["error"] = f"JSON parse failed; excerpt: {parsed.get('_raw_excerpt', '')[:200]}"
        return base

    base["path"] = parsed.get("path", [])
    base["vestigials"] = parsed.get("vestigials", [])
    base["loops"] = parsed.get("loops", [])
    base["dead_branches"] = parsed.get("dead_branches", [])
    base["ghost_refs"] = parsed.get("ghost_refs", [])
    base["recommendation"] = parsed.get("recommendation", "")
    base["ok"] = True
    return base


def _select_delegation_ids(burnless_root: Path, *, since_hours: int, limit: int) -> list[str]:
    """Return up to `limit` delegation IDs from the last `since_hours`, NEWEST FIRST.
       A delegation qualifies only if it falls inside the window AND has a
       corresponding .burnless/logs/<did>.log file."""
    delegations_dir = burnless_root / "delegations"
    logs_dir = burnless_root / "logs"
    if not delegations_dir.exists():
        return []
    cutoff = time.time() - since_hours * 3600
    specs = sorted(delegations_dir.glob("d*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    ids: list[str] = []
    for spec_path in specs:
        if len(ids) >= limit:
            break
        if spec_path.stat().st_mtime < cutoff:
            continue
        did = spec_path.stem
        if not (logs_dir / f"{did}.log").exists():
            continue
        ids.append(did)
    return ids


def sweep(burnless_root: Path, *, since_hours: int = 24, limit: int = 10, model: str = _DEFAULT_MODEL) -> list[dict]:
    """Trace the newest `limit` delegations from the last `since_hours` (newest first).
       Only delegations with a corresponding .burnless/logs/<did>.log are traced.
       Sequential, no parallel. Returns list of trace dicts."""
    ids = _select_delegation_ids(burnless_root, since_hours=since_hours, limit=limit)
    return [trace(burnless_root, did, model=model) for did in ids]


def write_capsule(trace_result: dict, burnless_root: Path) -> Path:
    """Write capsule to .burnless/debugless/debugless-trace-<did>-<YYYYMMDD>.md
       with frontmatter (name, summary, tags=debugless+trace+date, type) +
       body containing path/vestigials/loops/dead/ghost/recommendation sections.
       Returns path written."""
    did = trace_result.get("did", "unknown")
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    name = f"debugless-trace-{did}-{today}"
    summary = trace_result.get("recommendation") or f"Debugless trace for {did}"
    tags = ["debugless", "trace", today]

    lines = [
        "---",
        f"name: {name}",
        f"summary: {summary}",
        f"tags: {', '.join(tags)}",
        "type: trace",
        "---",
        "",
        f"# Debugless Trace — {did}",
        "",
        f"**model:** {trace_result.get('model_used', 'unknown')}  ",
        f"**ok:** {trace_result.get('ok')}  ",
        f"**error:** {trace_result.get('error') or 'none'}",
        "",
        "## Path",
        "",
    ]
    for step in trace_result.get("path", []):
        lines.append(f"- {step}")

    lines += ["", "## Vestigials", ""]
    for item in trace_result.get("vestigials", []):
        lines.append(f"- {item}")

    lines += ["", "## Loops", ""]
    for item in trace_result.get("loops", []):
        lines.append(f"- {item}")

    lines += ["", "## Dead Branches", ""]
    for item in trace_result.get("dead_branches", []):
        lines.append(f"- {item}")

    lines += ["", "## Ghost Refs", ""]
    for item in trace_result.get("ghost_refs", []):
        lines.append(f"- {item}")

    lines += ["", "## Recommendation", "", summary, ""]

    out_dir = burnless_root / "debugless"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
