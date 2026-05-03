"""
Burnless persistent chat mode (Fase A — Marco 6).

Difference from normal shell delegations:
  - chat does NOT create a delegation file or capsule per turn
  - chat builds a single prompt per turn that includes:
      · the project plan (compact)
      · imported MEMORY.md indexed at setup time, if present
      · last N turns of chat history
      · the new user message
  - chat uses the configured tier (sticky tier wins; default = gold)
  - chat appends every turn to .burnless/chat/chat.jsonl

Why a separate file: keeps chat state out of the delegations pipeline so a
chat session does not pollute the burnless_tokens counter or capsule store.
The economy comes from injecting MEMORY+history once, not from compression.

Public surface:
    run_chat(p, *, dry_run=False)
    build_prompt(p, user_message, history) -> str
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import agents as agents_mod
from . import config as config_mod
from . import state as state_mod


CHAT_BANNER = "Burnless chat — type /exit to leave, /clear to reset turns."
DEFAULT_HISTORY_TURNS = 6  # last N exchanges injected into the prompt
MEMORY_INJECT_LIMIT = 8000  # chars of MEMORY content max
PROJECT_MEMORY_FILES = ("MEMORY.md", "AGENTS.md", "CLAUDE.md")


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
            "user": user_msg,
            "assistant": assistant_msg,
        })


def build_prompt(p: dict[str, Path], user_msg: str, turns: Iterable[dict]) -> str:
    state = state_mod.load(p["state"])
    project = state.get("project", "Project")
    plan = state.get("plan") or ""
    memory_blob = _load_memory(p)

    # keep only the last N turns to bound the prompt
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


def _load_memory(p: dict[str, Path]) -> str:
    """Best-effort: collect MEMORY.md / AGENTS.md / CLAUDE.md found in:
       1. the project root (where .burnless lives)
       2. .burnless/memories/index.json source folders
    Truncates to MEMORY_INJECT_LIMIT chars total.
    """
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
