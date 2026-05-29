"""Maestro runner — invokes the burnless conducting layer (camada 2) isolated.

The Maestro is a pure dispatcher: it receives a compacted telegram of intent and
emits a single compacted telegram routing decision. It NEVER executes work, plans,
or inspects files. Stateless per call (no --resume); the fixed system prompt is the
cached prefix. Isolation flags cut the user's hooks and stabilize the cache — see
capsule burnless-maestro-cache-limpo-setting-sources.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from .preamble import system_prompt_with_suffix
from . import config

DEFAULT_MODEL = config.DEFAULT_TIER_MODELS["bronze"]

MAESTRO_SYSTEM_PROMPT = """You are MAESTRO, the conducting layer of the burnless orchestration system.

IDENTITY: You conduct. You do NOT perform work, you do NOT write plans or specs, you do NOT inspect files or execute commands. You receive a compacted telegram of user intent and decide the single next routing action.

You are NOT a Maestro and NOT a planner. Planning itself is delegated to a GOLD worker. You are a non-ambitious dispatcher: resist any urge to solve, explain, or elaborate.

PROTOCOL - input: one compacted telegram (one-line JSON) from the Telegrammer.
PROTOCOL - output: exactly one compacted telegram (one-line JSON). No prose. No markdown. No code fences. No preamble.

ROUTING DECISION (pick exactly one shape):
1. Intent needs a plan/spec and none exists yet:
   {"to":"gold","need":"plan","of":"<=12-word what to plan"}
2. A plan/spec was already provided in the telegram:
   {"to":"silver","run":"<=12-word spec summary"}   (use "bronze" if purely mechanical; "gold" NEVER executes)
3. A capsule/result indicates the work is done:
   {"done":"<=12-word result"}
4. Intent is trivial/conversational (no work):
   {"reply":"<=12-word answer"}
5. Action would touch CLIENT PRODUCTION, external infra, secrets, or an irreversible write, and no explicit user authorization is present yet:
   {"ask_user":"<=15-word what you are about to do, asking for OK"}

HARD RULES:
- Output ONE line of JSON. Nothing before it, nothing after it.
- Never write the actual plan or spec - that is gold's job.
- Never echo any worker output verbatim.
- Keep every string field <= 12 words.
- NEVER emit a "silver" or "bronze" routing for an action touching client production, external infra, secrets, or an irreversible write unless a PRIOR telegram in this exchange already carries explicit user authorization. When unsure, use ask_user first.
"""

# Tools that would let the model break role and try to execute work itself.
DISALLOWED_TOOLS = "Read,Edit,Write,NotebookEdit,Bash,Glob,Grep,LS,Task,WebFetch,WebSearch"

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _claude_bin() -> str:
    return shutil.which("claude") or "/opt/homebrew/bin/claude"


def strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def extract_telegram(result: str) -> str:
    """Return the last valid one-line JSON object in the model output.

    The Maestro model sometimes prepends reasoning prose; keep only the final
    JSON telegram. Falls back to fence-stripping if no JSON object parses.
    """
    import json as _json
    candidates = re.findall(r"\{[^{}]*\}", result)
    for cand in reversed(candidates):
        try:
            _json.loads(cand)
            return cand
        except ValueError:
            continue
    return strip_fences(result)


def build_command(telegram: str, model: str = DEFAULT_MODEL) -> list[str]:
    return [
        _claude_bin(), "-p", telegram,
        "--model", model,
        "--setting-sources", "project,local",
        "--exclude-dynamic-system-prompt-sections",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--system-prompt", system_prompt_with_suffix(MAESTRO_SYSTEM_PROMPT),
        "--tools", "",
        "--output-format", "json",
    ]


def run_maestro(telegram: str, model: str = DEFAULT_MODEL, timeout: int = 120) -> dict:
    """Invoke the Maestro layer once (stateless, isolated, cwd=/tmp).

    Returns dict: telegram_out (fence-stripped one-liner), raw, usage, cost, error?.
    """
    cmd = build_command(telegram, model)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd="/tmp"
        )
    except subprocess.TimeoutExpired:
        return {"telegram_out": "", "raw": "", "usage": {}, "cost": 0.0, "error": "timeout"}
    raw = (proc.stdout or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"telegram_out": "", "raw": raw, "usage": {}, "cost": 0.0, "error": "non_json_output"}
    result = (data.get("result") or "").strip()
    return {
        "telegram_out": extract_telegram(result),
        "raw": result,
        "usage": data.get("usage", {}) or {},
        "cost": data.get("total_cost_usd", 0.0),
    }
