"""burnless init --claude-code: opt-in installer for Claude Code agent files."""
from __future__ import annotations

import argparse
import json
import shutil
import stat
import sys
from pathlib import Path


_MANAGED = [
    ("agents/burnless-planner.md",   ".claude/agents/burnless-planner.md"),
    ("agents/burnless-worker.md",    ".claude/agents/burnless-worker.md"),
    ("hooks/burnless_compact_haiku.sh", ".claude/hooks/burnless_compact_haiku.sh"),
    ("scripts/burnless_mode_hook.sh", ".claude/scripts/burnless_mode_hook.sh"),
    ("scripts/burnless_session_seed.sh", ".claude/scripts/burnless_session_seed.sh"),
    ("scripts/burnless_offload_hook.sh", ".claude/scripts/burnless_offload_hook.sh"),
]

_NEXT_STEPS = """\
Next steps (opt-in, manual):
  1. Test agent: claude --agent burnless-planner "smoke test"
  2. To make burnless-planner the DEFAULT agent for all new sessions:
     edit ~/.claude/settings.json and add "agent": "burnless-planner"
  3. To enable the Claude Code engagement hook:
     add the hook entry in settings.json hooks.UserPromptSubmit
     and use /burnless off|partner|on|rollover in-session
"""


def is_wired(home: Path, templates_dir: Path | None = None) -> dict:
    """Read-only inspection of Claude Code hook wiring and managed file state."""
    settings_path = home / ".claude" / "settings.json"
    settings_exists = settings_path.exists()
    settings_parses = False
    sessionstart = False
    userprompt = False
    data: dict = {}

    if settings_exists:
        try:
            data = json.load(open(settings_path))
            settings_parses = True
        except Exception:
            pass

    if settings_parses:
        hooks = data.get("hooks", {})
        CMD = "bash ~/.claude/scripts/burnless_mode_hook.sh"
        ups = hooks.get("UserPromptSubmit", [])
        userprompt = any(
            CMD in h.get("command", "")
            for grp in ups
            for h in grp.get("hooks", [])
        )
        CMD2 = "bash ~/.claude/scripts/burnless_session_seed.sh"
        ss = hooks.get("SessionStart", [])
        sessionstart = any(
            CMD2 in h.get("command", "")
            for grp in ss
            for h in grp.get("hooks", [])
        )

    if templates_dir is None:
        templates_dir = _resolve_templates_dir()

    managed = []
    for src_rel, dst_rel in _MANAGED:
        dst = home / dst_rel
        if not dst.exists():
            state = "missing"
        elif templates_dir is not None:
            src = templates_dir / src_rel
            try:
                state = "match" if (src.exists() and dst.read_bytes() == src.read_bytes()) else "differs"
            except Exception:
                state = "differs"
        else:
            state = "differs"
        managed.append({"rel": dst_rel, "path": str(dst), "state": state})

    return {
        "settings_exists": settings_exists,
        "settings_parses": settings_parses,
        "sessionstart": sessionstart,
        "userprompt": userprompt,
        "managed": managed,
        "templates_dir": str(templates_dir) if templates_dir else None,
    }


def _resolve_templates_dir() -> Path | None:
    pkg_dir = Path(__file__).resolve().parent
    candidate = pkg_dir.parent.parent / "templates"
    if candidate.is_dir():
        return candidate
    candidate2 = pkg_dir.parent / "templates"
    if candidate2.is_dir():
        return candidate2
    try:
        import importlib.resources as _ir
        ref = Path(str(_ir.files("burnless"))) / ".." / "templates"
        ref = ref.resolve()
        if ref.is_dir():
            return ref
    except Exception:
        pass
    return None


def wire_settings_hook(home: Path) -> str:
    try:
        wired_info = is_wired(home)
        already_mode = wired_info["userprompt"]
        already_seed = wired_info["sessionstart"]
        if already_mode and already_seed:
            return "already-wired"

        settings_path = home / ".claude" / "settings.json"
        if settings_path.exists():
            data = json.load(open(settings_path))
        else:
            data = {}
        hooks = data.setdefault("hooks", {})
        ups = hooks.setdefault("UserPromptSubmit", [])
        ss = hooks.setdefault("SessionStart", [])
        CMD = "bash ~/.claude/scripts/burnless_mode_hook.sh"
        CMD2 = "bash ~/.claude/scripts/burnless_session_seed.sh"

        if not already_mode:
            ups.append({"hooks": [{"type": "command", "command": CMD, "timeout": 3}]})
        if not already_seed:
            ss.append({"hooks": [{"type": "command", "command": CMD2, "timeout": 10}]})
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        if settings_path.exists():
            bak_path = settings_path.parent / (settings_path.name + ".bak-burnless")
            shutil.copy2(settings_path, bak_path)
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return "wired"
    except Exception as e:
        return f"skip:{e}"


def unwire_settings_hook(home: Path) -> str:
    try:
        settings_path = home / ".claude" / "settings.json"
        if not settings_path.exists():
            return "not-wired"

        data = json.load(open(settings_path))
        hooks = data.setdefault("hooks", {})
        ups = hooks.setdefault("UserPromptSubmit", [])

        new_ups = []
        changed = False

        for grp in ups:
            new_hooks = []
            for h in grp.get("hooks", []):
                if "burnless_mode_hook.sh" not in h.get("command", ""):
                    new_hooks.append(h)
                else:
                    changed = True
            if new_hooks:
                grp["hooks"] = new_hooks
                new_ups.append(grp)

        ups[:] = new_ups
        ups = [grp for grp in ups if grp.get("hooks")]

        ss = hooks.setdefault("SessionStart", [])
        new_ss = []
        for grp in ss:
            new_hooks = []
            for h in grp.get("hooks", []):
                if "burnless_session_seed.sh" not in h.get("command", ""):
                    new_hooks.append(h)
                else:
                    changed = True
            if new_hooks:
                grp["hooks"] = new_hooks
                new_ss.append(grp)
        ss[:] = new_ss

        if not changed:
            return "not-wired"

        if settings_path.exists():
            bak_path = settings_path.parent / (settings_path.name + ".bak-burnless")
            shutil.copy2(settings_path, bak_path)

        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

        return "unwired"
    except Exception as e:
        return f"skip:{e}"


def run(args: argparse.Namespace) -> int:
    home = Path.home()
    templates_dir = _resolve_templates_dir()

    if args.uninstall:
        print("burnless init --claude-code --uninstall")
        for _, dst_rel in _MANAGED:
            dst = home / dst_rel
            tilde_dst = "~/" + dst_rel
            if dst.exists():
                dst.unlink()
                print(f"  removed: {tilde_dst}")
            else:
                print(f"  not present: {tilde_dst}")
        
        status = unwire_settings_hook(home)
        print(f"  hook unwiring: {status}")
        return 0

    if templates_dir is None:
        print(
            "burnless: templates directory not found. "
            "Re-install burnless or check your package layout.",
            file=sys.stderr,
        )
        return 1

    dry_run = bool(getattr(args, "dry_run", False))
    force = bool(getattr(args, "force", False))

    results: list[tuple[str, str]] = []

    for src_rel, dst_rel in _MANAGED:
        src = templates_dir / src_rel
        dst = home / dst_rel
        tilde_dst = "~/" + dst_rel

        if not src.exists():
            print(f"burnless: template missing: {src}", file=sys.stderr)
            return 1

        if dry_run:
            print(f"[dry-run] would install: {tilde_dst}")
            results.append(("would install", tilde_dst))
            continue

        if dst.exists():
            if dst.read_bytes() == src.read_bytes():
                print(f"  skipped: {tilde_dst}")
                results.append(("skipped", tilde_dst))
                continue
            if not force:
                print(f"  EXISTS_DIFFERENT: {tilde_dst}  (use --force to overwrite)")
                results.append(("EXISTS_DIFFERENT", tilde_dst))
                continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        src_mode = src.stat().st_mode
        if src_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  installed: {tilde_dst}")
        results.append(("installed", tilde_dst))

    if not dry_run:
        non_skipped = [(a, p) for a, p in results if a != "skipped"]
        print(f"\nburnless init --claude-code: {len(results)} file(s) processed")
        for action, path in non_skipped:
            print(f"  {action}: {path}")
        if not getattr(args, "no_wire", False):
            status = wire_settings_hook(home)
            print(f"  hook wiring: {status}")
        print()
        print(_NEXT_STEPS, end="")

    return 0
