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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import agents as agents_mod
from . import config as config_mod
from . import metrics as metrics_mod
from . import plugin_loader as plugin_loader_mod
from . import state as state_mod


CHAT_BANNER = "Burnless chat — /help for commands, /exit to leave."

_CHAT_HELP = """\
Commands:
  /help, /h        — this list
  /clear           — reset conversation turns (history stays in log)
  /model NAME      — switch model mid-session (e.g. claude-sonnet-4-6)
  /info            — current model, tier, turn count
  /exit, /quit     — leave chat
"""
DEFAULT_HISTORY_TURNS = 10
MEMORY_INJECT_LIMIT = 12_000
PROJECT_MEMORY_FILES = ("MEMORY.md", "AGENTS.md", "CLAUDE.md")

# Anthropic requires ≥1024 tokens for cache to activate (≥2048 for some models).
# This pad is byte-identical across all burnless sessions — it extends the core glossary
# to safely clear the threshold for all Claude models.
_CACHE_PAD = """

[burnless-protocol-extended-reference]

ARCHITECTURE
  User → Encoder LLM → Encoder Software → Maestro → Workers (gold/silver/bronze)
       → Decoder Software → Decoder LLM → User

  Encoder: Translates raw natural language to compact capsule format (~80 chars per turn).
           Default: cloud LLM (Haiku-class). Privacy alternative: local model (Ollama).

  Maestro: The persistent orchestrating agent. Receives ONLY capsules — never raw text.
           Maintains session state as a capsule history. Decides: respond directly |
           delegate to worker | ask for clarification. NEVER executes commands directly.

  Workers: Ephemeral execution agents. Receive a single task capsule with no conversation
           history. Three quality/cost tiers configurable by the user.

  Decoder: Translates capsule results back to natural language for the user.
           Default: cloud LLM (Haiku-class). Privacy alternative: local model (Ollama).

CAPSULE FORMAT
  {tier} {action} {target} :: {status} {detail} [ref:{exec_id}]

  Examples:
    gld imp auth/jwt :: OK schema+router+middleware done [ref:exec/T0042]
    slv doc api/     :: PART openapi.yaml done, examples pending [ref:exec/T0043]
    brz sum logs/    :: OK 3 errors found, 2 warnings [ref:exec/T0044]

  Status values: OK | PART | BLK | ERR

DELEGATION FORMAT
  del T{id} {tier} {action} {target} :: {spec}

  The dispatcher parses delegation lines, resolves the tier to the configured worker agent,
  and executes. Workers receive: (1) core glossary cached prefix, (2) worker role prompt
  cached prefix, (3) specific task capsule — single turn, no history.

COST MODEL
  N  = turns in session
  P  = persistent prefix tokens (system prompt)
  C  ≈ 20 tokens = capsule size (~80 chars)
  T  ≈ 1500 tokens = typical raw turn size

  Standalone: cost ≈ N·P·p_in + T·N(N-1)/2·p_in → Θ(N²)
  Burnless:   cost ≈ P·p_cw + (N-1)·P·p_cr + C·N(N-1)/2·p_in → Θ(N)

  The capsule term C·N(N-1)/2 is technically Θ(N²) but with constant C/T ≈ 0.013
  (~75x smaller). For N ≤ 1000 it remains below the linear cache-read term.

PRIVACY LEVELS (architectural consequence, not a mode flag)
  L0: Encoder=cloud, Maestro=cloud, Workers=cloud → providers see everything
  L1: Encoder=local, Maestro=cloud, Workers=cloud → providers see capsules only
  L2: Encoder=local, Maestro=local, Workers=cloud → providers see disconnected fragments
  L3: Encoder=local, Maestro=local, Workers=local → providers see nothing

  Level 2 is the strongest practical configuration for most users.
  Level 3 is the only configuration with a hard privacy guarantee.

GLOSSARY LAYERS
  1. Core glossary — fixed protocol terms, versioned with spec. Byte-identical across
     all users. Eligible for shared prefix caching (this block).
  2. Tenant/project glossary — local domain language per project (tenant_glossary.yaml).
  3. Session emergent glossary — append-only mappings proposed by encoder, validated
     by Maestro before adoption. Survives compaction as GLOSSARY_SUPERBLOCK.

CACHE ARCHITECTURE
  The Maestro system prompt is byte-identical every turn → persistent prefix caching.
  Cache read price ≈ 10x cheaper than standard input (100x cheaper than write).
  Model switching within same provider does NOT invalidate cache.
  Provider switching resets cache.

This block is byte-identical every session — it is the shared cache anchor.
Modification at runtime invalidates caching for all active sessions.
"""


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
    last_headers: list[dict] = [{}]  # mutable container for last response headers

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
        # --- slash commands ---
        if user_msg in {"/exit", "/quit", "exit", "quit", "/back", "back"}:
            return 0
        if user_msg in {"/clear", "clear"}:
            history.clear()
            print("(conversation reset)")
            continue
        if user_msg in {"/help", "/h", "/commands"}:
            print(_CHAT_HELP)
            continue
        if user_msg == "/info":
            print(f"model: {model}  tier: {tier}  turns: {len(history) // 2}")
            continue
        if user_msg in {"/usage", "/quota", "/limits"}:
            if last_headers[0]:
                print()
                print(_fmt_unified_usage(last_headers[0]))
                print()
            else:
                print("(no usage data yet — send a message first)")
            continue
        if user_msg.startswith("/model "):
            new_model = user_msg.removeprefix("/model ").strip()
            if new_model:
                model = new_model
                print(f"model → {model}")
            else:
                print("usage: /model <model-id>")
            continue
        if user_msg.startswith("/"):
            print(f"unknown command: {user_msg!r}  (try /help)")
            continue
        # --- end slash ---

        compressed, compression_info = _compress_chat_input(
            p,
            user_msg,
            hook_name="pre_brain_prompt",
        )

        history.append({"role": "user", "content": compressed})
        _chat_state = state_mod.load(p["state"])
        state_mod.touch_activity(_chat_state)
        state_mod.save(p["state"], _chat_state)

        # Trim to last N turns before sending so context window stays bounded.
        send_history = history[-(DEFAULT_HISTORY_TURNS * 2):]

        cache_read = cache_write = input_tok = 0
        print()
        sys.stdout.write("\033[2m(thinking…)\033[0m")
        sys.stdout.flush()
        try:
            with client.messages.stream(
                model=model,
                max_tokens=2048,
                system=system_blocks,
                messages=send_history,
                extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
            ) as stream:
                parts: list[str] = []
                first_token = True
                for text in stream.text_stream:
                    if first_token:
                        sys.stdout.write("\r\033[2K")
                        first_token = False
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    parts.append(text)
                print()
                final = stream.get_final_message()
                try:
                    last_headers[0] = dict(stream.response.headers)
                except Exception:
                    pass
            assistant_msg = "".join(parts)
            usage = final.usage
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            input_tok = getattr(usage, "input_tokens", 0) or 0
            output_tok = getattr(usage, "output_tokens", 0) or 0
            _record_chat_brain_usage(
                p,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_write,
                input_tokens=input_tok,
                output_tokens=output_tok,
                model=model,
            )
        except Exception as e:
            print(f"\nAPI error: {e}")
            history.pop()
            continue

        history.append({"role": "assistant", "content": assistant_msg})
        _chat_state = state_mod.load(p["state"])
        state_mod.touch_activity(_chat_state)
        state_mod.save(p["state"], _chat_state)

        hints: list[str] = []
        if cache_write > 0 and cache_read == 0:
            hints.append("\033[2m[cache written — next turn ~10x cheaper]\033[0m")
        elif cache_read > 0:
            saved_pct = int(cache_read / max(1, input_tok + cache_read) * 100)
            hints.append(f"\033[2m[cache hit — saved ~{saved_pct}% input cost]\033[0m")
        u5h = last_headers[0].get("anthropic-ratelimit-unified-5h-utilization")
        if u5h is not None:
            hints.append(f"\033[2m[session {int(float(u5h) * 100)}% used]\033[0m")
        if hints:
            print("  ".join(hints))
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
            "user_compressed": compressed,
            "compression": compression_info,
            "assistant": assistant_msg,
        })
    return 0


def _build_system_blocks(p: dict[str, Path], state: dict) -> list[dict]:
    plan = state.get("plan") or ""
    memory_blob = _load_memory(p)

    # Block 1: Core glossary — byte-identical across ALL burnless users worldwide.
    # This is the primary cache anchor: everyone shares the same prefix → Anthropic
    # can serve it from a single cache slot regardless of who calls.
    glossary = _load_glossary(p)

    # Block 2: Project-specific context (plan + memory). Changes per project, not per turn.
    project_context = ""
    if plan:
        project_context += f"[project plan]\n{plan.strip()}\n"
    if memory_blob:
        project_context += f"\n[user memory — read-only]\n{memory_blob}\n"

    blocks: list[dict] = [
        {
            "type": "text",
            "text": glossary,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]
    if project_context.strip():
        blocks.append(
            {
                "type": "text",
                "text": project_context,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        )
    return blocks


def _load_glossary(p: dict[str, Path]) -> str:
    """Load core glossary (byte-identical for all users) + pad to ensure ≥1024 tokens."""
    try:
        from .codec.glossary_loader import load_glossary
        text = load_glossary(p["root"].parent)
    except Exception:
        text = ""
    # Pad with protocol summary to ensure ≥2048 tokens (safe threshold for all models).
    # Claude 4 models require ~8000 chars (~2000 tokens) before cache activates.
    if len(text) < 8_000:
        text += _CACHE_PAD
    return text



def _resolve_model(tier: str, cfg: dict, state: dict) -> str:
    from .cli import MAESTRO_TIER_MODEL
    maestro_model = state.get("brain_model")  # legacy persisted key name (kept for on-disk back-compat); represents the Maestro layer
    if maestro_model:
        return maestro_model
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
    return _load_claude_oauth_token()


def _load_claude_oauth_token() -> str | None:
    """Read Claude Code OAuth token from macOS Keychain (fallback when no API key)."""
    try:
        import subprocess, json as _json
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        data = _json.loads(r.stdout.strip())
        return data.get("claudeAiOauth", {}).get("accessToken")
    except Exception:
        return None


def _fmt_usage_bar(pct: float, width: int = 20) -> str:
    filled = max(0, min(width, round(pct * width)))
    return "\033[34m" + "█" * filled + "\033[90m" + "░" * (width - filled) + "\033[0m"


def _fmt_unified_usage(headers: dict) -> str:
    """Format anthropic-ratelimit-unified-* headers into a /usage-style display."""
    from datetime import datetime, timezone as _tz

    def _reset_str(ts_raw: str) -> str:
        try:
            ts = int(ts_raw)
            dt = datetime.fromtimestamp(ts, tz=_tz.utc)
            secs = (dt - datetime.now(_tz.utc)).total_seconds()
            if secs <= 0:
                return "resetting…"
            if secs < 3600:
                m, s = divmod(int(secs), 60)
                return f"resets in {m}m{s:02d}s"
            if secs < 86400:
                h = int(secs // 3600)
                m = int((secs % 3600) // 60)
                return f"resets in {h}h{m:02d}m"
            local = dt.astimezone()
            return "resets " + local.strftime("%b %-d at %-I:%M%p").lower()
        except Exception:
            return ""

    lines: list[str] = []

    u5h = headers.get("anthropic-ratelimit-unified-5h-utilization")
    r5h = headers.get("anthropic-ratelimit-unified-5h-reset")
    if u5h is not None:
        pct = float(u5h)
        reset = _reset_str(r5h) if r5h else ""
        line = f"\033[1mCurrent session\033[0m   {_fmt_usage_bar(pct)}  {int(pct * 100)}% used"
        if reset:
            line += f" · {reset}"
        lines.append(line)

    u7d = headers.get("anthropic-ratelimit-unified-7d-utilization")
    r7d = headers.get("anthropic-ratelimit-unified-7d-reset")
    if u7d is not None:
        pct = float(u7d)
        reset = _reset_str(r7d) if r7d else ""
        line = f"\033[1mCurrent week\033[0m      {_fmt_usage_bar(pct)}  {int(pct * 100)}% used"
        if reset:
            line += f" · {reset}"
        lines.append(line)

    fallback = headers.get("anthropic-ratelimit-unified-fallback")
    fpct_raw = headers.get("anthropic-ratelimit-unified-fallback-percentage")
    overage_status = headers.get("anthropic-ratelimit-unified-overage-status")
    overage_reason = headers.get("anthropic-ratelimit-unified-overage-disabled-reason")
    if fallback == "available" and fpct_raw is not None:
        pct = float(fpct_raw)
        line = f"\033[1mExtra usage\033[0m       {_fmt_usage_bar(pct)}  {int(pct * 100)}% used"
        lines.append(line)
    elif overage_status == "rejected":
        reason = f" ({overage_reason})" if overage_reason else ""
        lines.append(f"\033[1mExtra usage\033[0m       \033[90munavailable{reason}\033[0m")

    return "\n".join(lines)


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

        compressed, compression_info = _compress_chat_input(
            p,
            user_msg,
            hook_name="pre_worker_prompt",
        )
        prompt = build_prompt(p, compressed, turns)
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

        turns.append({"user": compressed, "assistant": assistant_msg})
        _append_jsonl(log_path, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tier": tier,
            "agent": agent_cfg.get("name"),
            "backend": "subprocess",
            "user": user_msg,
            "user_compressed": compressed,
            "compression": compression_info,
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

def _compress_chat_input(
    p: dict[str, Path],
    user_msg: str,
    *,
    hook_name: str,
) -> tuple[str, dict]:
    """Free chat first layer: raw text -> minified/plugin-compressed text."""
    raw = user_msg or ""
    compressed = raw
    plugin_used = False
    try:
        from .codec.encoder import minify as _minify
        compressed = _minify(raw) or raw
    except Exception:
        compressed = raw

    try:
        plugins = plugin_loader_mod.load_plugins(Path.home() / ".burnless")
        if hook_name == "pre_brain_prompt":
            result = plugin_loader_mod.call_all_plugins(
                plugins,
                hook_name,
                {
                    "hook": hook_name,
                    "user_capsule": compressed,
                    "raw_user": raw,
                    "system_blocks": [],
                },
            )
            candidate = result.get("user_capsule") if result else None
        else:
            result = plugin_loader_mod.call_all_plugins(
                plugins,
                hook_name,
                {
                    "hook": hook_name,
                    "prompt": compressed,
                    "raw_user": raw,
                    "system_prompt": "",
                },
            )
            candidate = result.get("prompt") if result else None
        if isinstance(candidate, str) and candidate.strip():
            compressed = candidate.strip()
            plugin_used = True
    except Exception:
        pass

    info = _chat_compression_info(raw, compressed, plugin_used=plugin_used)
    _record_chat_compression(p, info)
    return compressed, info


def _chat_compression_info(raw: str, compressed: str, *, plugin_used: bool) -> dict:
    raw_chars = len(raw or "")
    compressed_chars = len(compressed or "")
    ratio = (raw_chars / compressed_chars) if compressed_chars else 1.0
    return {
        "raw_chars": raw_chars,
        "compressed_chars": compressed_chars,
        "ratio": round(ratio, 3),
        "plugin_used": plugin_used,
    }


def _record_chat_compression(p: dict[str, Path], info: dict) -> None:
    try:
        raw_chars = int(info.get("raw_chars", 0) or 0)
        compressed_chars = int(info.get("compressed_chars", 0) or 0)
        if raw_chars <= 0 or compressed_chars <= 0 or compressed_chars >= raw_chars:
            return
        metrics_mod.record_encoder_call(
            metrics_path=p["metrics"],
            audit_path=p["audit"],
            raw_input_chars=raw_chars,
            capsule_output_tokens=max(1, int(compressed_chars / 4)),
        )
        metrics_mod.bump_ratio_observed(p["metrics"], float(info.get("ratio", 1.0) or 1.0))
    except Exception:
        return


def _record_chat_brain_usage(
    p: dict[str, Path],
    *,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> None:
    try:
        metrics_mod.record_brain_call(
            metrics_path=p["metrics"],
            audit_path=p["audit"],
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
        )
    except Exception:
        return


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
