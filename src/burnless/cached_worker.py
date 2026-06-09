"""Burnless CachedWorker — executes delegations via Anthropic API with explicit cache_control.

This is the SDK path. The CLI path (`claude -p`) also has prefix-cache warmth —
Claude Code injects cache_control automatically with ephemeral_1h TTL — so this
module is not "the way to get cache." It is the way to control cache explicitly:
  - system blocks with cache_control={type: "ephemeral", ttl: "1h"}
  - a tool loop handling bash/read/write/ls/grep/glob in-process (no subprocess)
  - same dict output format as agents.run()
Use this when you need explicit control over cache breakpoints/TTL or want to
avoid spawning a subprocess per delegation. On the Claude Code monthly plan,
claude -p already maintains cache warmth — no SDK required for that benefit.

Cache layout (byte-identical across consecutive delegations):
  block 0: glossary.md  (ttl=1h)
  block 1: worker_role.md  (ttl=1h)
  block 2: static runtime context (cwd, state dir)  (ttl=1h)
  message: delegation text  ← varies per task, not cached

With a 23k-token prefix and silver tier (Sonnet), cache reads cost ~1.5 cents
per 1M tokens vs ~3 for uncached input — roughly 50% savings on the prefix
on every turn after the first.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_TOOL_OUTPUT = 40_000   # chars — truncate bash/read output
MAX_TURNS = 60             # safety cap on tool loop iterations
DEFAULT_MAX_TOKENS = 4096


# ── Tool definitions sent to the API ─────────────────────────────────────────

TOOLS: list[dict[str, Any]] = [
    {
        "name": "bash",
        "description": (
            "Execute a bash command in the working directory. "
            "Prefer non-interactive commands. Output is truncated at 40k chars."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from disk. Returns its full text content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a file. Creates parent directories automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files and directories at a path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: working dir)"},
            },
            "required": [],
        },
    },
    {
        "name": "glob_search",
        "description": "Find files matching a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. src/**/*.py"},
                "base_dir": {"type": "string", "description": "Base directory (default: working dir)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep_search",
        "description": "Search for a regex pattern in files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "File or directory to search (default: working dir)"},
                "include": {"type": "string", "description": "Glob to filter files, e.g. *.py"},
            },
            "required": ["pattern"],
        },
    },
]


# ── Tool execution ────────────────────────────────────────────────────────────

def _exec_tool(name: str, inputs: dict[str, Any], *, cwd: Path) -> str:
    try:
        if name == "bash":
            return _tool_bash(inputs.get("command", ""), cwd=cwd, timeout=int(inputs.get("timeout", 60)))
        if name == "read_file":
            return _tool_read(inputs.get("path", ""), cwd=cwd)
        if name == "write_file":
            return _tool_write(inputs.get("path", ""), inputs.get("content", ""), cwd=cwd)
        if name == "list_dir":
            return _tool_ls(inputs.get("path", ""), cwd=cwd)
        if name == "glob_search":
            return _tool_glob(inputs.get("pattern", "**/*"), inputs.get("base_dir", ""), cwd=cwd)
        if name == "grep_search":
            return _tool_grep(
                inputs.get("pattern", ""),
                inputs.get("path", "."),
                inputs.get("include", ""),
                cwd=cwd,
            )
        return f"unknown tool: {name}"
    except Exception as e:
        return f"tool error ({name}): {e}"


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT:
        return text
    half = MAX_TOOL_OUTPUT // 2
    return text[:half] + f"\n... [truncated {len(text) - MAX_TOOL_OUTPUT} chars] ...\n" + text[-half:]


def _tool_bash(command: str, *, cwd: Path, timeout: int = 60) -> str:
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True,
        cwd=str(cwd), timeout=min(timeout, 120),
    )
    out = result.stdout + (("\n[stderr]\n" + result.stderr) if result.stderr.strip() else "")
    if result.returncode != 0:
        out += f"\n[exit {result.returncode}]"
    return _truncate(out.strip())


def _tool_read(path: str, *, cwd: Path) -> str:
    p = Path(path) if Path(path).is_absolute() else cwd / path
    if not p.exists():
        return f"file not found: {p}"
    return _truncate(p.read_text(encoding="utf-8", errors="replace"))


def _tool_write(path: str, content: str, *, cwd: Path) -> str:
    p = Path(path) if Path(path).is_absolute() else cwd / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"written: {p} ({len(content)} chars)"


def _tool_ls(path: str, *, cwd: Path) -> str:
    p = Path(path) if (path and Path(path).is_absolute()) else (cwd / path if path else cwd)
    if not p.exists():
        return f"path not found: {p}"
    entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
    lines = []
    for e in entries:
        tag = "DIR" if e.is_dir() else "FILE"
        lines.append(f"{tag}  {e.name}")
    return "\n".join(lines) or "(empty)"


def _tool_glob(pattern: str, base: str, *, cwd: Path) -> str:
    base_p = Path(base) if (base and Path(base).is_absolute()) else (cwd / base if base else cwd)
    matches = sorted(base_p.glob(pattern))
    if not matches:
        return f"no files matched: {pattern}"
    return "\n".join(str(m.relative_to(base_p)) for m in matches[:200])


def _tool_grep(pattern: str, path: str, include: str, *, cwd: Path) -> str:
    cmd = ["grep", "-rn", "--include", include or "*", pattern, path or "."]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd), timeout=30)
        return _truncate(r.stdout.strip()) or "(no matches)"
    except Exception as e:
        return f"grep error: {e}"


# ── System prompt builder ─────────────────────────────────────────────────────

def _load_text(path: Path, fallback: str) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.exists() else fallback
    except Exception:
        return fallback


# Anthropic requires a minimum prefix size for cache to activate.
# Sonnet/Opus 3.5+: 1024 tokens. Haiku 3.5: 2048. Claude 4.x: 1024.
# We target 1024 as the safe minimum for Sonnet/gold tiers.
CACHE_MIN_TOKENS = 1024
# Conservative chars-per-token ratio for estimation (underestimates slightly)
_CHARS_PER_TOKEN = 3.5

_FALLBACK_GLOSSARY = """\
# Burnless Worker context
Tiers: gold=strategy, silver=execution, bronze=summaries.
Status: OK=complete, PART=partial, ERR=error, BLK=blocked.
"""

_FALLBACK_WORKER_ROLE = """\
You are a Burnless Worker. Execute the given task precisely using the available
tools. When done, emit a final JSON block matching the delegation's success schema.
"""


def _find_design_dir(project_root: Path) -> Path:
    """Locate maestro_v1 design dir: project-local first, then Burnless package."""
    local = project_root / "_design" / "maestro_v1"
    if local.is_dir():
        return local
    # Fall back to the design dir bundled with the Burnless package itself.
    pkg_design = Path(__file__).parent.parent.parent / "_design" / "maestro_v1"
    if pkg_design.is_dir():
        return pkg_design
    return local  # return non-existent path; _load_text uses fallback strings


def build_system_blocks(
    *, project_root: Path, burnless_root: Path, memory_index: Path | None = None,
    model: str = "",
) -> list[dict[str, Any]]:
    """Build a single cached system block with all static context.

    All three components (glossary, worker_role, runtime_context) are merged
    into one block with cache_control ttl=1h. A single breakpoint is simpler
    and guarantees the minimum token threshold is always met — multiple small
    breakpoints risk falling below the 1024-token cache minimum individually.

    The combined block is byte-identical across delegations for the same
    project, so every call after the first within the 1h TTL pays cache_read
    (~10% of input cost) instead of full input cost.
    """
    design_dir = _find_design_dir(project_root)

    glossary = _load_text(design_dir / "glossary.md", _FALLBACK_GLOSSARY)
    worker_role = _load_text(design_dir / "worker_role.md", _FALLBACK_WORKER_ROLE)

    mem_hint = (
        f"- Burnless memory index: {memory_index}\n"
        if memory_index and memory_index.exists()
        else "- Memory index: not present.\n"
    )
    static_context = (
        "## Runtime Context\n\n"
        f"- Working directory: {project_root}\n"
        f"- Burnless state: {burnless_root}\n"
        f"{mem_hint}"
        "- Use available tools (bash, read_file, write_file, list_dir, glob_search, grep_search) freely.\n"
        "- Emit the final JSON block at the end of your response (last lines of output).\n"
    )

    combined = "\n\n".join([glossary, worker_role, static_context])

    # Size the cache threshold by the worker's model (single source); fall back
    # to the module constant when model is empty/unknown. Local import to avoid
    # any import cycle with coreconfig.
    from .coreconfig.resolver import min_cache_tokens as _min_cache_tokens
    min_tokens = _min_cache_tokens(model) if model else CACHE_MIN_TOKENS

    # Safety check: warn if estimated tokens are below the API cache minimum.
    estimated_tokens = len(combined) / _CHARS_PER_TOKEN
    if estimated_tokens < min_tokens:
        import sys
        shortfall = int(min_tokens - estimated_tokens)
        print(
            f"[cached_worker] WARNING: system block ~{int(estimated_tokens)} tokens "
            f"(need {min_tokens} for cache). Adding {shortfall * int(_CHARS_PER_TOKEN)} "
            "chars of padding to activate cache.",
            file=sys.stderr,
        )
        # Pad with a harmless comment to reach the minimum threshold.
        # Use ceil division with float ratio + 25% safety margin to avoid
        # the off-by-one from int truncation.
        pad_chars = int(shortfall / _CHARS_PER_TOKEN * 4) + 128  # 128 extra as margin
        combined += "\n\n<!-- burnless-cache-pad " + ("." * pad_chars) + " -->"

    return [{"type": "text", "text": combined, "cache_control": {"type": "ephemeral", "ttl": "1h"}}]


def bust_cache(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of blocks with a unique nonce appended to the first block.

    Forces a cache miss on the next API call — useful for cold-cache benchmarks.
    The nonce is a UTC timestamp so the text changes on every call.
    """
    import copy
    from datetime import datetime, timezone
    busted = copy.deepcopy(blocks)
    nonce = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    busted[0]["text"] += f"\n\n<!-- cache-bust:{nonce} -->"
    return busted


# ── Main entry point ──────────────────────────────────────────────────────────

def run_cached_worker(
    *,
    prompt: str,
    model: str,
    project_root: Path,
    burnless_root: Path,
    api_key: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout_s: int = 600,
    log_path: Path | None = None,
    cold_cache: bool = False,
) -> dict[str, Any]:
    """Run a delegation via Anthropic API with cached system prompt + tool loop.

    Returns a dict shaped like agents.run():
      stdout, stderr, returncode, started_at, ended_at, duration_s, interrupted
    Plus extra keys:
      _cached_worker: True
      _usage: {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}
    """
    import anthropic

    started = datetime.now(timezone.utc)
    start_mono = time.monotonic()

    client = anthropic.Anthropic(api_key=api_key)
    memory_index = burnless_root / "memories" / "index.json"
    system = build_system_blocks(
        project_root=project_root,
        burnless_root=burnless_root,
        memory_index=memory_index,
        model=model,
    )
    if cold_cache:
        system = bust_cache(system)
        import sys as _sys
        print("[cached_worker] cold_cache=True — nonce injected, cache miss guaranteed", file=_sys.stderr)

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    stdout_parts: list[str] = []
    usage_totals: dict[str, int] = {}
    interrupted = False
    log_lines: list[str] = []

    def _log(line: str) -> None:
        log_lines.append(line)
        if log_path:
            try:
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    _log(f"# cached_worker started model={model} timeout={timeout_s}s")
    _log(f"# started_at: {started.isoformat()}")

    for turn in range(MAX_TURNS):
        if time.monotonic() - start_mono > timeout_s:
            _log(f"# timeout after {timeout_s}s at turn {turn}")
            interrupted = True
            break

        try:
            response = client.messages.create(
                model=model,
                system=system,
                messages=messages,
                tools=TOOLS,
                max_tokens=max_tokens,
                extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
            )
        except Exception as e:
            _log(f"# API error: {e}")
            return _build_result(
                stdout="", stderr=str(e), returncode=1,
                started=started, start_mono=start_mono,
                usage=usage_totals, interrupted=False, log_lines=log_lines,
            )

        # Accumulate usage
        u = response.usage
        for field in ("input_tokens", "output_tokens",
                      "cache_creation_input_tokens", "cache_read_input_tokens",
                      "cache_creation_input_tokens_5min", "cache_creation_input_tokens_1h"):
            v = getattr(u, field, None) or 0
            usage_totals[field] = usage_totals.get(field, 0) + v

        # Extract text blocks from this response
        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        if text_parts:
            stdout_parts.extend(text_parts)
            _log("[stdout] " + "".join(text_parts))

        if response.stop_reason == "end_turn":
            _log(f"# end_turn at turn {turn+1}")
            break

        if response.stop_reason == "tool_use":
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            tool_results = []
            for tu in tool_uses:
                _log(f"[tool] {tu.name}({json.dumps(tu.input, ensure_ascii=False)[:200]})")
                result_text = _exec_tool(tu.name, tu.input, cwd=project_root)
                _log(f"[tool_result] {result_text[:300]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result_text,
                })
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            continue

        # max_tokens or other stop
        _log(f"# stop_reason={response.stop_reason} at turn {turn+1}")
        break

    stdout = "".join(stdout_parts)
    ended = datetime.now(timezone.utc)

    cache_read = usage_totals.get("cache_read_input_tokens", 0)
    cache_write = (
        usage_totals.get("cache_creation_input_tokens_1h", 0)
        + usage_totals.get("cache_creation_input_tokens_5min", 0)
        + usage_totals.get("cache_creation_input_tokens", 0)
    )
    _log(f"# ended_at: {ended.isoformat()}")
    _log(f"# usage: input={usage_totals.get('input_tokens',0)} output={usage_totals.get('output_tokens',0)} cache_read={cache_read} cache_write={cache_write}")

    # Real-time Maestro metrics — best effort, never blocks the result path.
    try:
        from . import metrics as _metrics_mod
        from . import paths as _paths_mod

        # Walk up from project_root to find the .burnless dir.
        bl_root = None
        for candidate in [project_root, *project_root.parents]:
            if (candidate / ".burnless").is_dir():
                bl_root = candidate / ".burnless"
                break
            if candidate.name == ".burnless":
                bl_root = candidate
                break
        if bl_root is not None:
            _p = _paths_mod.paths_for(bl_root)
            _metrics_mod.record_brain_call(
                metrics_path=_p["metrics"],
                audit_path=_p["audit"],
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_write,
                input_tokens=usage_totals.get("input_tokens", 0),
                output_tokens=usage_totals.get("output_tokens", 0),
                model=model,
            )
    except Exception:
        pass  # Observability is never load-bearing.

    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(log_lines), encoding="utf-8")

    return _build_result(
        stdout=stdout, stderr="", returncode=0,
        started=started, start_mono=start_mono,
        usage=usage_totals, interrupted=interrupted, log_lines=log_lines,
    )


def _build_result(
    *, stdout: str, stderr: str, returncode: int,
    started: datetime, start_mono: float,
    usage: dict[str, int], interrupted: bool, log_lines: list[str],
) -> dict[str, Any]:
    ended = datetime.now(timezone.utc)
    return {
        "agent": "cached_worker",
        "command": ["cached_worker"],
        "stdout": stdout,
        "stderr": stderr,
        "returncode": returncode,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_s": time.monotonic() - start_mono,
        "interrupted": interrupted,
        "_cached_worker": True,
        "_usage": usage,
    }


def is_available(api_key: str | None) -> bool:
    """True if anthropic SDK is installed and an API key is present."""
    if not api_key:
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False
