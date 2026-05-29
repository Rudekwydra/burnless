"""3-layer pipeline: Maestro subprocess management.

User flow:
  IDE Haiku (encoder/decoder layer) → mcp__burnless__maestro(envelope) → this module
  → subprocess Sonnet (persistent session) → response_envelope → back to IDE Haiku

Cache strategy: Maestro session resumed across calls; HARD RULES system prompt
goes in EVERY user message (cached prefix) to enforce "always delegate" habit.
"""
from __future__ import annotations
import json
import re
import subprocess
import threading
from pathlib import Path

MAESTRO_HARD_RULES = """[MAESTRO ROLE — read this every turn]

You are the Maestro in a 3-layer Burnless pipeline. You receive ENVELOPES
(compressed user intent) from a Haiku encoder. You respond with envelope-shaped
output that a Haiku decoder will expand for the user.

HARD RULES — never break, even if "cheaper" to break:
1. NEVER read files directly. Delegate: run `burnless do --tier bronze "read X and report Y"`.
2. NEVER run shell commands directly beyond `burnless do` / `burnless run` / `burnless capsule`.
3. NEVER decode envelope to natural language. Output stays in envelope/structured form.
4. For ANY action larger than 2 lines of reasoning: STOP. Delegate to a worker.

OUTPUTS ALLOWED:
  A. Tool calls (Bash) limited to: burnless do / burnless run / burnless capsule / burnless read
  B. Final response as JSON object:
     {
       "response_envelope": "<terse summary of decision/result>",
       "key_facts": [...],
       "delegations_made": ["d042", "d043"],
       "next": "<what's next or empty>"
     }

If tempted to read/execute directly: that is "escapulida". Always delegate.
Habit > micro-optimization. Better to lose 1/50 than skip the protocol.
"""


_maestro_sessions: dict[str, str] = {}
_lock = threading.Lock()


def _build_user_message(envelope: str, compression_mode: str) -> str:
    return (
        MAESTRO_HARD_RULES
        + f"\n\n[COMPRESSION MODE: {compression_mode}]"
        + "\n\n[INCOMING ENVELOPE FROM ENCODER]\n"
        + envelope
        + "\n\n[END ENVELOPE — process and respond per HARD RULES]"
    )


def _parse_stream_json(stdout: str) -> tuple[str | None, str, dict]:
    """Extract (session_id, final_text, usage_metrics) from claude stream-json output."""
    session_id: str | None = None
    final_text = ""
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "duration_ms": 0,
        "model": None,
    }
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = obj.get("type")
        if t == "system" and "session_id" in obj and not session_id:
            session_id = obj["session_id"]
            usage["model"] = obj.get("model") or usage["model"]
        elif t == "result":
            final_text = obj.get("result", "") or final_text
            u = obj.get("usage") or {}
            usage["input_tokens"] += int(u.get("input_tokens", 0))
            usage["output_tokens"] += int(u.get("output_tokens", 0))
            usage["cache_creation_input_tokens"] += int(u.get("cache_creation_input_tokens", 0))
            usage["cache_read_input_tokens"] += int(u.get("cache_read_input_tokens", 0))
            usage["duration_ms"] = int(obj.get("duration_ms", 0)) or usage["duration_ms"]
    return session_id, final_text, usage


def _try_extract_envelope_json(text: str) -> dict | None:
    """Best-effort extract the final JSON envelope from Maestro output."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    brace_start = text.rfind("{")
    while brace_start != -1:
        try:
            return json.loads(text[brace_start:])
        except json.JSONDecodeError:
            brace_start = text.rfind("{", 0, brace_start)
    return None


def process_envelope(
    envelope: str,
    project_root: Path,
    compression_mode: str = "tight",
    model: str | None = None,
    timeout: int = 180,
) -> dict:
    """Send envelope to persistent Maestro subprocess; return structured result + decoder hint."""
    from . import config as _config, state as _state, paths as _paths
    if model is None:
        try:
            st = _state.load(_paths.paths_for(project_root / ".burnless")["state"])
            model = st.get("brain_model") or _config.DEFAULT_TIER_MODELS["silver"]
        except Exception:
            model = _config.DEFAULT_TIER_MODELS["silver"]
    key = str(project_root.resolve())
    with _lock:
        session_id = _maestro_sessions.get(key)

    cmd = [
        "/opt/homebrew/bin/claude", "-p",
        "--model", model,
        "--permission-mode", "bypassPermissions",
        "--allowedTools", "Bash",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    if session_id:
        cmd += ["--resume", session_id]

    user_message = _build_user_message(envelope, compression_mode)
    try:
        proc = subprocess.run(
            cmd,
            input=user_message,
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "error": "maestro_timeout",
            "decoder_hint": "Tell the user the Maestro timed out. Suggest retry or smaller request.",
        }

    new_session_id, final_text, usage = _parse_stream_json(proc.stdout)

    if new_session_id and not session_id:
        with _lock:
            _maestro_sessions[key] = new_session_id

    response_envelope_json = _try_extract_envelope_json(final_text)

    decoder_hint = (
        "Translate the envelope to natural language for the user. "
        "Be terse. Preserve tone markers. Respect trauma_block if set. "
        "Do not add commentary or filler."
    )
    if compression_mode == "loose":
        decoder_hint += " You may expand with light context where helpful."

    resp_text = json.dumps(response_envelope_json) if response_envelope_json else (final_text or "")
    compression_telemetry = {
        "envelope_chars": len(envelope),
        "response_chars": len(resp_text),
        "maestro_model": model,
        "ratio": round(len(envelope) / max(len(resp_text), 1), 3),
    }

    return {
        "response_envelope": response_envelope_json or {"raw_text": final_text},
        "decoder_hint": decoder_hint,
        "compression_mode": compression_mode,
        "maestro_session_id": new_session_id or session_id,
        "maestro_exit_code": proc.returncode,
        "usage": usage,
        "compression": compression_telemetry,
    }
