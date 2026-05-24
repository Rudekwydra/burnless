"""Debugless: GoPro-trace analyser for burnless delegations.

Reads .burnless/delegations/<did>.md + .burnless/logs/<did>.log,
calls local ollama to identify vestigials/loops/dead branches/ghost refs,
and writes a capsule to .burnless/debugless/.

Invoked manually via `burnless trace <did>` or `burnless debugless sweep`.
All external dependencies: stdlib only + ollama CLI subprocess.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

_LOG_TAIL_CHARS = 8000
_DEFAULT_MODEL = "gemma3:27b-cloud"


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
    """Invoke `ollama run <model>` via subprocess. stdin=prompt, capture stdout.
       Returns (raw_output, exit_code). On timeout: returns ('', -1)."""
    try:
        # e.g.: ollama run qwen2.5-coder:7b
        env = {**os.environ, "TERM": "dumb", "NO_COLOR": "1"}
        result = subprocess.run(
            ["ollama", "run", model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        return "", -1
    except FileNotFoundError:
        return "", -2


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


def trace(burnless_root: Path, did: str, *, model: str = "gemma3:27b-cloud", timeout: int = 90) -> dict:
    """Trace a single delegation. Returns dict with keys:
       did, model_used, path (list[str]), vestigials (list[str]), loops (list[str]),
       dead_branches (list[str]), ghost_refs (list[str]), recommendation (str),
       ok (bool), error (str | None).
       Calls ollama via subprocess (`ollama run <model> <prompt>`)."""
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
        base["error"] = "ollama binary not found"
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


def sweep(burnless_root: Path, *, since_hours: int = 24, limit: int = 10, model: str = "gemma3:27b-cloud") -> list[dict]:
    """Iterate over .burnless/delegations/d*.md whose mtime < since_hours ago,
       call trace() for each (sequential, no parallel), return list of trace dicts.
       Skip delegations without a corresponding .burnless/logs/<did>.log file.
       limit caps results."""
    delegations_dir = burnless_root / "delegations"
    logs_dir = burnless_root / "logs"
    if not delegations_dir.exists():
        return []

    cutoff = time.time() - since_hours * 3600
    results = []

    for spec_path in sorted(delegations_dir.glob("d*.md"), key=lambda p: p.stat().st_mtime):
        if len(results) >= limit:
            break
        if spec_path.stat().st_mtime > cutoff:
            continue
        did = spec_path.stem
        log_path = logs_dir / f"{did}.log"
        if not log_path.exists():
            continue
        result = trace(burnless_root, did, model=model)
        results.append(result)

    return results


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
