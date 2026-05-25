"""burnless init --claude-code: opt-in installer for Claude Code agent files."""
from __future__ import annotations

import argparse
import shutil
import stat
import sys
from pathlib import Path


_MANAGED = [
    ("agents/burnless-planner.md",   ".claude/agents/burnless-planner.md"),
    ("agents/burnless-worker.md",    ".claude/agents/burnless-worker.md"),
    ("hooks/burnless_compact_haiku.sh", ".claude/hooks/burnless_compact_haiku.sh"),
]

_NEXT_STEPS = """\
Next steps (opt-in, manual):
  1. Test agent: claude --agent burnless-planner "smoke test"
  2. To make burnless-planner the DEFAULT agent for all new sessions:
     edit ~/.claude/settings.json and add "agent": "burnless-planner"
  3. To enable layer-1 haiku compactor: touch ~/.burnless/compactor_enabled
     and add the hook entry in settings.json hooks.UserPromptSubmit
"""


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
        print()
        print(_NEXT_STEPS, end="")

    return 0
