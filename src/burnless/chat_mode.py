"""
Burnless persistent chat mode.

/chat in the shell enters this mode. Two backends:

1. SDK backend (default when ANTHROPIC_API_KEY is set):
   - System prompt with plan+memory marked cache_control ephemeral 1h
   - Real prefix-cache warmth: 2nd turn costs ~10x less than 1st
   - Conversation history in user messages (not system) so cache stays stable

2. Subprocess fallback (when no API key or anthropic lib):
   - Calls the configured worker CLI (claude -p, codex, etc.)
   - No cache_control — each turn is a fresh call

Public surface:
    run_chat(p, *, dry_run=False)
    build_prompt(p, user_message, history) -> str   (subprocess path, kept for tests)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import agents as agents_mod
from . import config as config_mod
from . import state as state_mod


CHAT_BANNER = "Burnless chat — /exit to leave, /clear to reset turns."
DEFAULT_HISTORY_TURNS = 10
MEMORY_INJECT_LIMIT = 12_000
PROJECT_MEMORY_FILES = ("MEMORY.md", "AGENTS.md", "CLAUDE.md")

# Anthropic requires ≥1024 tokens for cache to activate.
# Memory + plan usually exceeds that; if not, we pad with the protocol summary.
_CACHE_PAD = (
    "\n\n[burnless-protocol-context]\n"
    "Burnless reduces multi-turn LLM cost from Θ(N²) to Θ(N) via capsule memory "
    "and shared prefix caching. Three tiers: gold (strategy/architecture), "
    "silver (implementation/docs), bronze (summarize/classify/extract). "
    "Workers receive isolated task capsules — no conversation history. "
    "The Encoder compresses raw turns; the Decoder expands capsules back to natural language. "
    "Privacy is a consequence of where each component runs: "
    "L0=all cloud, L1=local encoder, L2=local maestro, L3=all local. "
    "Cache key = content of cached block, not model name. "
    "Switching models within the same provider recovers warm cache within one turn. "
    "Capsule format: {tier} {action} {target} :: {status} {detail} [ref:{exec_id}]. "
    "Status values: OK | PART | BLK | ERR. "
    "Delegation format: del T{id} {tier} {action} {target} :: {spec}. "
    "This context block is byte-identical every session — it is the cache anchor. "
    "Do not modify this block at runtime.\n"
)


def run_chat(p: dict[str, Path], *, dry_run: bool | None = None) -> int:
    if dry_run is None:
        dry_run = os.environ.get("BURNLESS_CHAT_DRYRUN") in ("1", "true", "yes")

    chat_dir = p["chat"]
    chat_dir.mkdir(parents=True, exist_ok=True)
    log_path = chat_dir / "chat.jsonl"

    cfg = config_mod.load(p["config"])
    state = state_mod.load(p["state"])
    tier = state.get("active_tier") or "gold"
    if tier not in cfg.get("agents", {}):
        tier = "gold"
    agent_cfg = cfg["agents"][tier]

    print()
    print(CHAT_BANNER)

    if not dry_run:
        api_key = _load_api_key(p)
        if api_key:
            return _run_chat_sdk(p, tier, agent_cfg, api_key, log_path)

    print(f"agent: {tier}/{agent_cfg.get('name')}    dry_run: {dry_run}")
    print()
    return _run_chat_subprocess(p, tier, agent_cfg, log_path, dry_run=bool(dry_run))


# ---- SDK backend (cache-warm) -------------------------------------------

def _run_chat_sdk(
    p: dict[str, Path],
    tier: str,
    agent_cfg: dict,
    api_key: str,
    log_path: Path,
) -> int:
    try:
        import anthropic as anthropic_mod
    except ImportError:
        return _run_chat_subprocess(p, tier, agent_cfg, log_path)

    client = anthropic_mod.Anthropic(api_key=api_key)
    state = state_mod.load(p["state"])
    cfg = config_mod.load(p["config"])

    model = _resolve_model(tier, cfg, state)
    system_blocks = _build_system_blocks(p, state)
    history: list[dict] = []

    print(f"agent: {tier}/{agent_cfg.get('name')}  model: {model}  cache: enabled")
    print()

    while True:
        try:
            user_msg = input(f"chat [{tier}] › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not user_msg:
            continue
        if user_msg in {"/exit", "/quit", "exit", "quit", "/back", "back"}:
            return 0
        if user_msg in {"/clear", "clear"}:
            history = []
            print("(conversation reset)")
            continue

        history.append({"role": "user", "content": user_msg})
        try:
            response = client.messages.create(
                model=model,
                max_tokens=2048,
                system=system_blocks,
                messages=history,
                extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
            )
            assistant_msg = _extract_text(response)
            usage = response.usage
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            input_tok = getattr(usage, "input_tokens", 0) or 0
            hint = ""
            if cache_write > 0 and cache_read == 0:
                hint = f"  \033[2m[cache written — next turn ~10x cheaper]\033[0m"
            elif cache_read > 0:
                saved_pct = int(cache_read / max(1, input_tok + cache_read) * 100)
                hint = f"  \033[2m[cache hit — saved ~{saved_pct}% input cost]\033[0m"
        except Exception as e:
            print(f"API error: {e}")
            history.pop()
            continue

        history.append({"role": "assistant", "content": assistant_msg})

        print()
        print(assistant_msg)
        print(hint)
        print()

        _append_jsonl(log_path, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tier": tier,
            "model": model,
            "backend": "sdk",
            "cache_read": cache_read,
            "cache_write": cache_write,
            "input_tokens": input_tok,
            "user": user_msg,
            "assistant": assistant_msg,
        })


def _build_system_blocks(p: dict[str, Path], state: dict) -> list[dict]:
    plan = state.get("plan") or ""
    memory_blob = _load_memory(p)
    project_docs = _load_project_docs(p)

    # Stable anchor — byte-identical across sessions; must be ≥1024 tokens for cache to activate
    anchor = _CACHE_PAD
    if project_docs:
        anchor += f"\n\n[project documentation]\n{project_docs}\n"
    if plan:
        anchor += f"\n[project plan]\n{plan.strip()}\n"
    if memory_blob:
        anchor += f"\n[user memory — read-only]\n{memory_blob}\n"

    return [
        {
            "type": "text",
            "text": anchor,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def _load_project_docs(p: dict[str, Path]) -> str:
    """Load protocol/vision docs to ensure system block ≥1024 tokens for cache activation."""
    project_root = p["root"].parent
    doc_names = ("VISION.md", "PROTOCOL.md", "README.md")
    parts: list[str] = []
    char_budget = 8_000
    for name in doc_names:
        path = project_root / name
        if not path.exists():
            continue
        text = _safe_read(path)
        if not text:
            continue
        take = min(len(text), char_budget)
        parts.append(f"## {name}\n{text[:take]}")
        char_budget -= take
        if char_budget <= 0:
            break
    return "\n\n".join(parts)


def _resolve_model(tier: str, cfg: dict, state: dict) -> str:
    from .cli import MAESTRO_TIER_MODEL
    brain_model = state.get("brain_model")
    if brain_model:
        return brain_model
    agent_name = cfg.get("agents", {}).get(tier, {}).get("name", "")
    tier_map = {
        "gold": "claude-opus-4-7",
        "silver": "claude-sonnet-4-6",
        "bronze": "claude-haiku-4-5-20251001",
    }
    return MAESTRO_TIER_MODEL.get(tier, tier_map.get(tier, "claude-sonnet-4-6"))


def _extract_text(response) -> str:
    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    return "(empty response)"


def _load_api_key(p: dict[str, Path]) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    for candidate in (
        Path.home() / ".config" / "burnless" / "anthropic.env",
        p["root"] / "anthropic.env",
    ):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("ANTHROPIC_API_KEY="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return None


# ---- subprocess fallback ------------------------------------------------

def _run_chat_subprocess(
    p: dict[str, Path],
    tier: str,
    agent_cfg: dict,
    log_path: Path,
    *,
    dry_run: bool = False,
) -> int:
    print(f"agent: {tier}/{agent_cfg.get('name')}    dry_run: {dry_run}")
    print()
    turns: list[dict] = []
    while True:
        try:
            user_msg = input(f"chat [{tier}] › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not user_msg:
            continue
        if user_msg in {"/exit", "/quit", "exit", "quit", "/back", "back"}:
            return 0
        if user_msg in {"/clear", "clear"}:
            turns = []
            print("(local turns reset; chat.jsonl preserved)")
            continue

        prompt = build_prompt(p, user_msg, turns)
        if dry_run:
            print("--- [dry_run prompt] ---")
            print(prompt)
            print("--- [end] ---")
            assistant_msg = "(dry_run: no agent invoked)"
        else:
            try:
                result = agents_mod.run(agent_cfg, prompt, timeout=180)
            except agents_mod.AgentError as e:
                print(f"agent error: {e}")
                continue
            assistant_msg = (result.get("stdout") or "").strip()
            if not assistant_msg:
                assistant_msg = "(empty response)"
            print()
            print(assistant_msg)
            print()

        turns.append({"user": user_msg, "assistant": assistant_msg})
        _append_jsonl(log_path, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tier": tier,
            "agent": agent_cfg.get("name"),
            "backend": "subprocess",
            "user": user_msg,
            "assistant": assistant_msg,
        })


# ---- kept for tests -----------------------------------------------------

def build_prompt(p: dict[str, Path], user_msg: str, turns: Iterable[dict]) -> str:
    state = state_mod.load(p["state"])
    project = state.get("project", "Project")
    plan = state.get("plan") or ""
    memory_blob = _load_memory(p)

    turns_list = list(turns)[-DEFAULT_HISTORY_TURNS:]

    parts: list[str] = []
    parts.append(f"You are the assistant inside the Burnless shell for project '{project}'.")
    parts.append("Be concise. Match the user's language. No preamble.")
    if plan:
        parts.append("\n[project plan]\n" + plan.strip())
    if memory_blob:
        parts.append("\n[user memory — read-only context]\n" + memory_blob)
    if turns_list:
        parts.append("\n[recent conversation]")
        for t in turns_list:
            parts.append(f"user: {t['user']}")
            parts.append(f"assistant: {t['assistant']}")
    parts.append("\n[new message]")
    parts.append(f"user: {user_msg}")
    parts.append("assistant:")
    return "\n".join(parts)


# ---- helpers ------------------------------------------------------------

def _load_memory(p: dict[str, Path]) -> str:
    blobs: list[str] = []
    project_root = p["root"].parent
    for name in PROJECT_MEMORY_FILES:
        candidate = project_root / name
        if candidate.exists():
            blobs.append(_safe_read(candidate))

    index_path = p["root"] / "memories" / "index.json"
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        sources: set[str] = set()
        for f in (data.get("files") or []):
            src = f.get("source")
            if src:
                sources.add(src)
        for src in sources:
            for name in PROJECT_MEMORY_FILES:
                candidate = Path(src) / name
                if candidate.exists():
                    blobs.append(_safe_read(candidate))

    blob = "\n\n---\n\n".join(b for b in blobs if b)
    if len(blob) <= MEMORY_INJECT_LIMIT:
        return blob
    return blob[:MEMORY_INJECT_LIMIT] + "\n…[truncated]"


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
