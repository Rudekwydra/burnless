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
import json
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
    ("codex",  "codex exec --skip-git-repo-check --model gpt-5.5 -c model_reasoning_effort=low --sandbox workspace-write", "openai/codex agent"),
    ("claude", "claude -p",                                                  "anthropic agent"),
    ("gemini", "gemini -p",                                                  "google agent"),
    ("openai", "openai",                                                     "openai cli"),
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
    models: list[str] = field(default_factory=list)

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
            if name == "codex":
                info.models = _try_codex_models(name)
            elif name == "ollama":
                info.models = _try_ollama_models(name)
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


def _try_codex_models(binary: str) -> list[str]:
    try:
        proc = subprocess.run(
            [binary, "debug", "models"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    out: list[str] = []
    for model in models:
        if isinstance(model, dict) and isinstance(model.get("slug"), str):
            out.append(model["slug"])
    return out


def _try_ollama_models(binary: str) -> list[str]:
    try:
        proc = subprocess.run(
            [binary, "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if proc.returncode != 0:
        return []
    lines = (proc.stdout or "").splitlines()
    out: list[str] = []
    for line in lines[1:]:
        parts = line.split()
        if parts:
            out.append(parts[0])
    return out


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

    codex = det.clis.get("codex", CliInfo("codex"))
    claude = det.clis.get("claude", CliInfo("claude"))
    gemini = det.clis.get("gemini", CliInfo("gemini"))
    ollama = det.clis.get("ollama", CliInfo("ollama"))

    if codex.available:
        return _suggest_codex(codex, base)

    if claude.available:
        out["gold"]["name"] = "opus"
        out["gold"]["command"] = f"claude --model {config_mod.DEFAULT_TIER_MODELS['gold']} --allowedTools Read,Edit,Write,Bash,Glob,Grep,LS,WebFetch -p --output-format stream-json --verbose --include-partial-messages"
        out["silver"]["name"] = "sonnet"
        out["silver"]["command"] = f"claude --model {config_mod.DEFAULT_TIER_MODELS['silver']} --allowedTools Read,Edit,Write,Bash,Glob,Grep,LS -p --output-format stream-json --verbose --include-partial-messages"
        out["bronze"]["name"] = "haiku"
        out["bronze"]["command"] = f"claude --model {config_mod.DEFAULT_TIER_MODELS['bronze']} --allowedTools Read,Bash,Glob,Grep,LS -p --output-format stream-json --verbose --include-partial-messages"
    elif gemini.available:
        out["gold"]["name"] = "gemini-pro"
        out["gold"]["command"] = "gemini -p --model gemini-pro"
        out["silver"]["name"] = "gemini-flash"
        out["silver"]["command"] = "gemini -p --model gemini-flash"
        out["bronze"]["name"] = "gemini-flash-lite"
        out["bronze"]["command"] = "gemini -p --model gemini-flash-lite"
    elif ollama.available and ollama.models:
        model = _prefer_model(ollama.models, [config_mod.DEFAULT_LOCAL_MODEL, "qwen2.5-coder", "llama3.2", "llama3", "mistral"])
        for tier, role in (
            ("gold", "local_strategy_reasoning"),
            ("silver", "local_execution"),
            ("bronze", "local_summaries_classification"),
        ):
            out[tier]["name"] = f"ollama-{model}"
            out[tier]["command"] = f"ollama run {model}"
            out[tier]["role"] = role

    return out


def _suggest_codex(codex: CliInfo, base: dict) -> dict:
    out = {tier: dict(base[tier]) for tier in base}
    models = codex.models
    gold_model = _prefer_model(models, ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex"])
    silver_model = _prefer_model(models, ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex"])
    bronze_model = _prefer_model(models, ["gpt-5.4-mini", "gpt-5.3-codex", silver_model])

    out["gold"].update(
        {
            "name": f"codex-{gold_model}-medium",
            "command": _codex_command(gold_model, effort="medium", sandbox="workspace-write"),
            "role": "strategy_architecture_code_review",
            "use_for": ["architecture", "complex_reasoning", "high_level_planning"],
        }
    )
    out["silver"].update(
        {
            "name": f"codex-{silver_model}-low",
            "command": _codex_command(silver_model, effort="low", sandbox="workspace-write"),
            "role": "everyday_execution_filesystem",
            "use_for": ["code", "docs", "debug", "tests", "repository_inspection"],
        }
    )
    out["bronze"].update(
        {
            "name": f"codex-{bronze_model}-low",
            "command": _codex_command(bronze_model, effort="low", sandbox="read-only"),
            "role": "summaries_classification_readonly",
            "use_for": ["summarize", "classify", "inspect", "clean_logs"],
        }
    )
    return out


def _prefer_model(models: list[str], candidates: list[str]) -> str:
    if not models:
        return candidates[0]
    for candidate in candidates:
        if candidate in models:
            return candidate
    return models[0]


def _codex_command(model: str, *, effort: str, sandbox: str) -> str:
    return (
        "codex exec --skip-git-repo-check "
        f"--model {model} -c model_reasoning_effort={effort} "
        f"--sandbox {sandbox}"
    )


# ----- rendering -----

def render_detection(det: Detection) -> str:
    lines: list[str] = []
    lines.append("Detected CLIs:")
    for name, info in det.clis.items():
        marker = "✓" if info.available else "·"
        if info.available:
            ver = f"  ({info.version})" if info.version else ""
            lines.append(f"  {marker} {name:<8} {info.path}{ver}")
            if info.models:
                preview = ", ".join(info.models[:4])
                extra = "..." if len(info.models) > 4 else ""
                lines.append(f"      models: {preview}{extra}")
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
        print("  · install one of: codex, claude, gemini, ollama")
        print("  · or set ANTHROPIC_API_KEY / OPENAI_API_KEY and install a matching CLI")
        print()

    rec = suggest(det)
    print(render_recommendation(rec))
    print()

    if not (non_interactive or accept_all):
        if not _confirm("Use this mapping?", default=True):
            print("Aborted. No files written.")
            return 1
    default_tier = _choose_default_tier(non_interactive=non_interactive, accept_all=accept_all)
    effective_tier = default_tier

    if fresh_project:
        for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
            p[key].mkdir(parents=True, exist_ok=True)
        from . import metrics as metrics_mod
        config_mod.write_default(p["config"])
        initial_state = dict(state_mod.DEFAULT_STATE)
        initial_state["project"] = project or cwd.name
        initial_state["active_tier"] = effective_tier
        state_mod.save(p["state"], initial_state)
        metrics_mod.save(p["metrics"], metrics_mod._fresh())
        p["maestro"].write_text(
            f"# Maestro — {initial_state['project']}\n\n_No plan yet._\n",
            encoding="utf-8",
        )
        p["history"].write_text("# Burnless Chat History\n", encoding="utf-8")

    # Tiers are global, never per-project. Seed the global tier map from the
    # detected recommendation only when the global has none yet (never clobber an
    # existing global). The project config carries no agents block; it cascades.
    import yaml as _yaml
    gp = config_mod.global_config_path()
    gdata = {}
    if gp.exists():
        try:
            gdata = _yaml.safe_load(gp.read_text(encoding="utf-8")) or {}
        except Exception:
            gdata = {}
    if not gdata.get("agents"):
        gdata["agents"] = rec
        gp.parent.mkdir(parents=True, exist_ok=True)
        gp.write_text(_yaml.safe_dump(gdata, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _pcfg = {}
    try:
        _pcfg = _yaml.safe_load(p["config"].read_text(encoding="utf-8")) or {}
    except Exception:
        _pcfg = {}
    if "agents" in _pcfg:
        _pcfg.pop("agents", None)
        p["config"].write_text(_yaml.safe_dump(_pcfg, sort_keys=False, allow_unicode=True), encoding="utf-8")

    if not fresh_project:
        current_state = state_mod.load(p["state"])
        if default_tier is not None or not (non_interactive or accept_all or not sys.stdin.isatty()):
            current_state["active_tier"] = default_tier
        effective_tier = current_state.get("active_tier")
        state_mod.save(p["state"], current_state)

    print()
    print(f"✓ config written: {p['config']}")
    print(f"✓ project: {(project or cwd.name)}")
    print(f"✓ default shell tier: {effective_tier or 'auto'}")
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

    from datetime import datetime as _dt
    from . import SETUP_VERSION as _SETUP_VERSION, __version__ as _bv
    _meta_path = config_mod.global_config_path().parent / "setup_meta.json"
    _existing: dict = {}
    if _meta_path.exists():
        try:
            _existing = json.loads(_meta_path.read_text(encoding="utf-8"))
        except Exception:
            _existing = {}
    if _existing.get("setup_version") != _SETUP_VERSION or _existing.get("burnless_version") != _bv:
        setup_meta = {
            "setup_version": _SETUP_VERSION,
            "burnless_version": _bv,
            "wired_at": _dt.utcnow().isoformat(),
            "mcp_registered": False,
        }
        _meta_path.parent.mkdir(parents=True, exist_ok=True)
        with open(_meta_path, "w", encoding="utf-8") as _f:
            json.dump(setup_meta, _f, indent=2)

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


def _choose_default_tier(*, non_interactive: bool, accept_all: bool) -> str | None:
    """Choose the shell's initial tier. None means automatic routing."""
    if non_interactive or accept_all or not sys.stdin.isatty():
        return None
    prompt = "Default shell tier? auto/gold/silver/bronze [auto] "
    while True:
        try:
            ans = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not ans or ans in {"auto", "a"}:
            return None
        if ans in {"gold", "silver", "bronze"}:
            return ans
        print("Choose one of: auto, gold, silver, bronze.")


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


def _register_mcp(meta_path: Path) -> bool:
    """Register burnless MCP server with Claude Code. Fail-open."""
    def _save_flag(registered: bool) -> None:
        existing: dict = {}
        if meta_path.exists():
            try:
                existing = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing["mcp_registered"] = registered
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    if not shutil.which("claude"):
        _save_flag(False)
        print("  MCP not registered; run: claude mcp add burnless -- python -m burnless.mcp_server")
        return False

    try:
        proc = subprocess.run(
            ["claude", "mcp", "add", "burnless", "--", sys.executable, "-m", "burnless.mcp_server"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        stderr = proc.stderr or ""
        if proc.returncode == 0 or "already exists" in stderr:
            _save_flag(True)
            return True
        _save_flag(False)
        print("  MCP not registered; run: claude mcp add burnless -- python -m burnless.mcp_server")
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        _save_flag(False)
        print("  MCP not registered; run: claude mcp add burnless -- python -m burnless.mcp_server")
        return False


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
