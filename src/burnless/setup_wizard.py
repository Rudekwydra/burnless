"""
Burnless setup wizard.

Goal: a user installing burnless for the first time should be productive in
under 60 seconds. We detect what they already have (CLIs, API keys, memory
folders), suggest a tier mapping, and write a sensible config without making
them read the schema.

Public surface:
    detect()        — pure detection, no IO besides reading
    suggest(det)    — turn detection into a recommended config
    run(...)        — interactive wizard that wraps detect+suggest+confirm

The wizard is idempotent: running it on an existing project updates the
config in place and never deletes user customisations.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import config as config_mod
from . import paths as paths_mod
from . import state as state_mod


# (cli_name, default_command, role_hint)
_KNOWN_CLIS: tuple[tuple[str, str, str], ...] = (
    ("codex",  "codex exec --skip-git-repo-check --sandbox workspace-write", "code execution"),
    ("claude", "claude -p",                                                  "anthropic agent"),
    ("gemini", "gemini -p",                                                  "google agent"),
    ("openai", "openai api chat.completions.create",                         "openai cli"),
    ("ollama", "ollama run",                                                 "local llm"),
)

_KNOWN_API_ENV: tuple[tuple[str, str], ...] = (
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("OPENAI_API_KEY",    "openai"),
    ("GEMINI_API_KEY",    "google"),
    ("GOOGLE_API_KEY",    "google"),
    ("MISTRAL_API_KEY",   "mistral"),
    ("GROQ_API_KEY",      "groq"),
)

# Likely places where the user already keeps AI memories. We only scan; we
# never read content unless the user opts in.
_MEMORY_HINTS: tuple[str, ...] = (
    "~/.claude/projects",
    "~/.claude/memory",
    "~/.codex",
    "~/.config/claude",
    "~/Documents/AI",
    "~/Documents/notes",
    "~/notes",
)


@dataclass
class CliInfo:
    name: str
    path: str | None = None
    version: str | None = None
    default_command: str = ""

    @property
    def available(self) -> bool:
        return bool(self.path)


@dataclass
class Detection:
    clis: dict[str, CliInfo] = field(default_factory=dict)
    api_keys: dict[str, bool] = field(default_factory=dict)
    memory_paths: list[Path] = field(default_factory=list)
    cwd: Path = field(default_factory=Path.cwd)

    @property
    def has_anything(self) -> bool:
        return any(c.available for c in self.clis.values()) or any(self.api_keys.values())


# ----- detection -----

def detect(*, scan_memory: bool = True) -> Detection:
    """Pure-ish detection: returns Detection without prompting the user."""
    clis: dict[str, CliInfo] = {}
    for name, default_cmd, _role in _KNOWN_CLIS:
        path = shutil.which(name)
        info = CliInfo(name=name, path=path, default_command=default_cmd)
        if path:
            info.version = _try_version(name)
        clis[name] = info

    api_keys: dict[str, bool] = {}
    for env_var, provider in _KNOWN_API_ENV:
        if os.environ.get(env_var):
            api_keys[provider] = True
        else:
            api_keys.setdefault(provider, False)

    memory_paths: list[Path] = []
    if scan_memory:
        for hint in _MEMORY_HINTS:
            p = Path(hint).expanduser()
            if p.exists():
                memory_paths.append(p)

    return Detection(clis=clis, api_keys=api_keys, memory_paths=memory_paths)


def _try_version(binary: str) -> str | None:
    """Best-effort: capture --version. Skip if it would be slow or interactive."""
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    return out[0][:80] if out else None


# ----- suggestion -----

def suggest(det: Detection) -> dict:
    """Map detection to a recommended agents config block.

    Strategy:
      gold    → strongest available strategic model
      silver  → everyday execution/coding model (Codex if present)
      bronze  → cheapest summarization/classification model

    Falls back gracefully when nothing's available — returns the package
    defaults unchanged so the user can edit later.
    """
    base = config_mod.DEFAULT_CONFIG["agents"]
    out = {tier: dict(base[tier]) for tier in base}

    if det.clis.get("claude", CliInfo("claude")).available:
        out["gold"]["name"] = "opus"
        out["gold"]["command"] = "claude --model claude-opus-4-7 --allowedTools Read,Edit,Write,Bash,Glob,Grep,LS,WebFetch -p"
        out["silver"]["name"] = "sonnet"
        out["silver"]["command"] = "claude --model claude-sonnet-4-6 --allowedTools Read,Edit,Write,Bash,Glob,Grep,LS -p"
        out["bronze"]["name"] = "haiku"
        out["bronze"]["command"] = "claude --model claude-haiku-4-5-20251001 --allowedTools Read,Bash,Glob,Grep,LS -p"
    if det.clis.get("codex", CliInfo("codex")).available and not det.clis.get("claude", CliInfo("claude")).available:
        out["silver"]["name"] = "codex"
        out["silver"]["command"] = det.clis["codex"].default_command
        out["silver"]["role"] = "everyday_code_execution"
    elif not det.clis.get("claude", CliInfo("claude")).available and det.clis.get("gemini", CliInfo("gemini")).available:
        out["gold"]["name"] = "gemini-pro"
        out["gold"]["command"] = "gemini -p --model gemini-pro"
        out["silver"]["name"] = "gemini-flash"
        out["silver"]["command"] = "gemini -p --model gemini-flash"
        out["bronze"]["name"] = "gemini-flash-lite"
        out["bronze"]["command"] = "gemini -p --model gemini-flash-lite"

    return out


# ----- rendering -----

def render_detection(det: Detection) -> str:
    lines: list[str] = []
    lines.append("Detected CLIs:")
    for name, info in det.clis.items():
        marker = "✓" if info.available else "·"
        if info.available:
            ver = f"  ({info.version})" if info.version else ""
            lines.append(f"  {marker} {name:<8} {info.path}{ver}")
        else:
            lines.append(f"  {marker} {name:<8} not on PATH")
    lines.append("")
    lines.append("Detected API keys:")
    for provider, present in det.api_keys.items():
        marker = "✓" if present else "·"
        lines.append(f"  {marker} {provider}")
    if det.memory_paths:
        lines.append("")
        lines.append("Found memory locations:")
        for p in det.memory_paths:
            lines.append(f"  · {p}")
    return "\n".join(lines)


def render_recommendation(rec: dict) -> str:
    lines = ["Recommended tier mapping:"]
    for tier in ("gold", "silver", "bronze"):
        a = rec[tier]
        lines.append(f"  {tier:<8} → {a['name']:<14} ({a['command']})")
    return "\n".join(lines)


# ----- wizard -----

def run(*, non_interactive: bool = False, accept_all: bool = False, project: str | None = None) -> int:
    """Run the wizard. Returns process exit code."""
    cwd = Path.cwd()
    root = paths_mod.root(cwd)
    p = paths_mod.paths_for(root)
    fresh_project = not root.exists()

    print("Burnless setup")
    print("==============")
    print()

    det = detect()
    print(render_detection(det))
    print()

    if not det.has_anything:
        print("No CLIs or API keys detected.")
        print("Burnless still works, but you'll need to either:")
        print("  · install one of: codex, claude, gemini")
        print("  · or set ANTHROPIC_API_KEY / OPENAI_API_KEY in your shell")
        print()

    rec = suggest(det)
    print(render_recommendation(rec))
    print()

    if not (non_interactive or accept_all):
        if not _confirm("Use this mapping?", default=True):
            print("Aborted. No files written.")
            return 1

    if fresh_project:
        for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
            p[key].mkdir(parents=True, exist_ok=True)
        from . import metrics as metrics_mod
        config_mod.write_default(p["config"])
        initial_state = dict(state_mod.DEFAULT_STATE)
        initial_state["project"] = project or cwd.name
        state_mod.save(p["state"], initial_state)
        metrics_mod.save(p["metrics"], metrics_mod._fresh())
        p["maestro"].write_text(
            f"# Maestro — {initial_state['project']}\n\n_No plan yet._\n",
            encoding="utf-8",
        )
        p["history"].write_text("# Burnless Chat History\n", encoding="utf-8")

    cfg = config_mod.load(p["config"])
    cfg["agents"] = rec
    config_mod.save(p["config"], cfg)

    print()
    print(f"✓ config written: {p['config']}")
    print(f"✓ project: {(project or cwd.name)}")
    print()

    if det.memory_paths and not non_interactive:
        do_index = accept_all or _confirm(
            f"Index {len(det.memory_paths)} existing memory folder(s) for future use?",
            default=False,
        )
        if do_index:
            indexed = _index_memories(det.memory_paths, p)
            print(f"  indexed {indexed} memory file(s) → {p['root'] / 'memories'}")
        else:
            print("  skipped — you can run `burnless setup` again later.")

    print()
    print("Done. Try:  burnless")
    return 0


# ----- helpers -----

def _confirm(prompt: str, *, default: bool) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    if not sys.stdin.isatty():
        return default
    try:
        ans = input(prompt + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not ans:
        return default
    return ans in ("y", "yes", "s", "sim")


def _index_memories(roots: Iterable[Path], paths: dict) -> int:
    """Light-touch indexing: collect a list of memory file paths into a
    json index. We do NOT copy or compress yet — that's Fase C."""
    import json
    targets: list[dict] = []
    for root in roots:
        for ext in ("*.md", "*.MD"):
            for f in root.rglob(ext):
                try:
                    size = f.stat().st_size
                except OSError:
                    continue
                if size > 1_000_000:  # skip pathological files
                    continue
                targets.append({
                    "path": str(f),
                    "size": size,
                    "source": str(root),
                })
    out_dir = paths["root"] / "memories"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "index.json"
    index_path.write_text(json.dumps({
        "version": 1,
        "imported_at": _now_iso(),
        "files": targets,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(targets)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
