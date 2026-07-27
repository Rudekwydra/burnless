"""Idempotent Codex integration installer.

Manages the Burnless block in ``~/.codex/AGENTS.md`` plus global lifecycle
hooks in ``~/.codex/hooks.json``.  Hook registration deliberately uses the
separate JSON source supported by Codex so setup never has to parse or rewrite
the user's ``~/.codex/config.toml``.
"""
from __future__ import annotations

import difflib
import json
import re
import shlex
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BLOCK_START = "<!-- burnless:codex:start -->"
BLOCK_END = "<!-- burnless:codex:end -->"
BLOCK_PATTERN = re.compile(
    r"<!-- burnless:codex:start[^>]*-->.*?<!-- burnless:codex:end -->",
    re.DOTALL,
)

HOOK_FILES = (
    "burnless_epoch_session.sh",
    "burnless_epoch_stop.sh",
    "burnless_epoch_end.sh",
    "codex_payload.sh",
)
_HOOK_EVENT_FILES = {
    "SessionStart": "burnless_epoch_session.sh",
    "Stop": "burnless_epoch_stop.sh",
    "SessionEnd": "burnless_epoch_end.sh",
}
_HOOK_TIMEOUTS = {
    "SessionStart": 10,
    "Stop": 10,
    # Codex caps SessionEnd command hooks at three seconds.
    "SessionEnd": 3,
}


def render_block(version: str) -> str:
    """Emit a SHORT, self-contained Burnless policy block for Codex's AGENTS.md.

    Self-contained on purpose: AGENTS.md is global (~/.codex/AGENTS.md), so the
    block points at the installed CLI (`burnless --help`) rather than
    repo-relative doc paths that may not apply to whatever project Codex is
    currently working in.
    """
    return f"""<!-- burnless:codex:start -->
<!-- burnless version: v{version} -->
## Burnless — orchestration active for Codex

Prefer delegating work with `burnless do "<spec>"` (atomic) over editing files
directly; use `burnless delegate "<spec>"` then `burnless run <id>` for staged
execution.

- **Worker specs must use absolute paths** (for example `/Users/.../file.py`);
  relative paths fail in the worker's isolated working directory.
- **Gold/Diamond tier tasks:** prefer `burnless ask --tier gold/diamond` for
  planning, architecture, and irreversible-decision arbitration — this is
  currently a RECOMMENDATION, not an enforced requirement.
- **Rolling memory & `/clear` (Codex) — this SAVES THE USER TOKENS:**
  `burnless setup --codex` installs global lifecycle hooks for restore, turn
  capture, and end-of-session handoff. Open `/hooks` once in Codex and trust
  the Burnless hooks (re-trust after a hook upgrade); untrusted hooks are
  skipped. Once trusted, Burnless persists a handoff to disk and restores your
  working state next session, so the user can `/clear` (or close Codex) and
  lose nothing. Proactively tell the user this and suggest `/clear` when the
  context window grows large: a bloated context bills the user for tokens they
  do not need to spend, so NOT clearing wastes their money and degrades your
  answers. Never imply `/clear` loses work — with Burnless it does not.
- **When to delegate:** conversational questions and quick single-file reads —
  just answer. Multi-file or spec-able changes — prefer `burnless do`.
- **Language:** these operating instructions are in English. ALWAYS reply to
  the user in the USER'S language.
- **Reference:** run `burnless --help` for current commands and flags.
<!-- burnless:codex:end -->"""


def _updated_agents_content(agents_md_path: Path, version: str) -> tuple[str, str]:
    """Return ``(action, content)`` without writing the target."""
    block = render_block(version)
    if not agents_md_path.exists():
        return "created", block + "\n"

    existing = agents_md_path.read_text(encoding="utf-8")
    if BLOCK_PATTERN.search(existing):
        new_content = BLOCK_PATTERN.sub(block, existing)
        action = "unchanged" if new_content == existing else "updated"
        return action, new_content

    return "appended", existing + "\n\n" + block + "\n"


def write_or_update(agents_md_path: Path, version: str) -> str:
    """Write the burnless block to AGENTS.md.

    Returns one of: "created", "updated", "appended".
    - "created": file did not exist; created with block as sole content
    - "updated": file had existing burnless block; replaced in place
    - "appended": file existed without block; appended at end
    - "unchanged": the current managed block already matches
    """
    action, content = _updated_agents_content(agents_md_path, version)
    if action != "unchanged":
        agents_md_path.parent.mkdir(parents=True, exist_ok=True)
        agents_md_path.write_text(content, encoding="utf-8")
    return action


def remove_block(agents_md_path: Path) -> bool:
    """Remove the burnless block from AGENTS.md. Returns True if a block was removed."""
    if not agents_md_path.exists():
        return False
    existing = agents_md_path.read_text(encoding="utf-8")
    if not BLOCK_PATTERN.search(existing):
        return False
    new_content = BLOCK_PATTERN.sub("", existing).rstrip() + "\n"
    agents_md_path.write_text(new_content, encoding="utf-8")
    return True


def _resolve_hooks_templates_dir() -> Path | None:
    """Find Codex hook templates in a source tree or installed wheel."""
    package_dir = Path(__file__).resolve().parent
    for templates_dir in (
        package_dir.parent.parent / "templates",
        package_dir.parent / "templates",
        package_dir / "templates",
    ):
        candidate = templates_dir / "codex" / "hooks"
        if candidate.is_dir():
            return candidate
    return None


def _burnless_hook_group(event: str, hooks_dir: Path) -> dict[str, Any]:
    filename = _HOOK_EVENT_FILES[event]
    script_path = (hooks_dir / filename).resolve()
    handler: dict[str, Any] = {
        "type": "command",
        "command": f"bash {shlex.quote(str(script_path))}",
        "timeout": _HOOK_TIMEOUTS[event],
    }
    if event == "SessionStart":
        handler["statusMessage"] = "Restoring Burnless rolling memory"
    elif event == "Stop":
        handler["statusMessage"] = "Capturing Burnless rolling memory"
    else:
        handler["statusMessage"] = "Writing Burnless session handoff"

    group: dict[str, Any] = {}
    if event == "SessionStart":
        group["matcher"] = "startup|resume|clear|compact"
    group["hooks"] = [handler]
    return group


def _is_burnless_handler(handler: Any) -> bool:
    if not isinstance(handler, dict):
        return False
    command = handler.get("command")
    return isinstance(command, str) and any(name in command for name in _HOOK_EVENT_FILES.values())


def _without_burnless_handlers(groups: Any) -> Any:
    """Remove only Burnless handlers while preserving every unrelated entry."""
    if not isinstance(groups, list):
        return groups
    cleaned: list[Any] = []
    for group in groups:
        if not isinstance(group, dict):
            cleaned.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            cleaned.append(group)
            continue
        kept = [handler for handler in handlers if not _is_burnless_handler(handler)]
        if kept:
            clone = dict(group)
            clone["hooks"] = kept
            cleaned.append(clone)
    return cleaned


def merge_hooks_document(
    existing: dict[str, Any] | None,
    *,
    hooks_dir: Path | None = None,
) -> dict[str, Any]:
    """Return a registration document with exactly one group per Burnless hook."""
    if hooks_dir is None:
        hooks_dir = Path.home() / ".codex" / "hooks"
    document: dict[str, Any] = dict(existing or {})
    if not document:
        document["description"] = "Global lifecycle hooks installed by Burnless."

    hooks_value = document.get("hooks", {})
    if hooks_value is None:
        hooks_value = {}
    if not isinstance(hooks_value, dict):
        raise ValueError("~/.codex/hooks.json field 'hooks' must be a JSON object")

    hooks: dict[str, Any] = dict(hooks_value)
    for event, groups in list(hooks.items()):
        hooks[event] = _without_burnless_handlers(groups)
    for event in _HOOK_EVENT_FILES:
        hooks.setdefault(event, [])
        if not isinstance(hooks[event], list):
            raise ValueError(f"~/.codex/hooks.json field hooks.{event} must be a JSON array")
        hooks[event].append(_burnless_hook_group(event, hooks_dir))
    document["hooks"] = hooks
    return document


@dataclass(frozen=True)
class FileChange:
    path: Path
    before: bytes | None
    after: bytes
    executable: bool = False
    backup: bool = False
    mode_before: int | None = None

    @property
    def content_changed(self) -> bool:
        return self.before != self.after

    @property
    def mode_changed(self) -> bool:
        return self.executable and (
            self.mode_before is None
            or not self.mode_before & stat.S_IXUSR
            or not self.mode_before & stat.S_IXGRP
            or not self.mode_before & stat.S_IXOTH
        )

    @property
    def changed(self) -> bool:
        return self.content_changed or self.mode_changed


@dataclass(frozen=True)
class SetupPlan:
    changes: tuple[FileChange, ...]
    agents_action: str

    @property
    def changed(self) -> bool:
        return any(change.changed for change in self.changes)


def _read_optional(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def plan_setup(home: Path, version: str) -> SetupPlan:
    """Build the complete setup plan without writing any files."""
    templates_dir = _resolve_hooks_templates_dir()
    if templates_dir is None:
        raise FileNotFoundError("Burnless Codex hook templates directory not found")

    for filename in HOOK_FILES:
        if not (templates_dir / filename).is_file():
            raise FileNotFoundError(f"Burnless Codex hook template missing: {filename}")

    codex_dir = home / ".codex"
    agents_path = codex_dir / "AGENTS.md"
    agents_action, agents_content = _updated_agents_content(agents_path, version)
    changes: list[FileChange] = [
        FileChange(
            path=agents_path,
            before=_read_optional(agents_path),
            after=agents_content.encode("utf-8"),
        )
    ]

    hooks_dir = codex_dir / "hooks"
    for filename in HOOK_FILES:
        source = templates_dir / filename
        target = hooks_dir / filename
        changes.append(
            FileChange(
                path=target,
                before=_read_optional(target),
                after=source.read_bytes(),
                executable=True,
                mode_before=target.stat().st_mode if target.exists() else None,
            )
        )

    hooks_json_path = codex_dir / "hooks.json"
    hooks_json_before = _read_optional(hooks_json_path)
    if hooks_json_before is None:
        existing_document: dict[str, Any] = {}
    else:
        try:
            parsed = json.loads(hooks_json_before.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot parse {hooks_json_path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{hooks_json_path} must contain a top-level JSON object")
        existing_document = parsed

    merged_document = merge_hooks_document(existing_document, hooks_dir=hooks_dir)
    hooks_json_after = (
        json.dumps(merged_document, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    changes.append(
        FileChange(
            path=hooks_json_path,
            before=hooks_json_before,
            after=hooks_json_after,
            backup=hooks_json_before is not None,
        )
    )
    return SetupPlan(changes=tuple(changes), agents_action=agents_action)


def format_plan_diff(plan: SetupPlan) -> str:
    """Render unified content diffs plus executable-mode changes."""
    sections: list[str] = []
    for change in plan.changes:
        if change.content_changed:
            before_text = (change.before or b"").decode("utf-8", errors="replace").splitlines(
                keepends=True
            )
            after_text = change.after.decode("utf-8", errors="replace").splitlines(keepends=True)
            before_name = str(change.path) if change.before is not None else "/dev/null"
            diff = "".join(
                difflib.unified_diff(
                    before_text,
                    after_text,
                    fromfile=before_name,
                    tofile=str(change.path),
                )
            )
            sections.append(diff.rstrip())
        if change.mode_changed:
            sections.append(f"mode change: make executable {change.path}")
    return "\n\n".join(section for section in sections if section)


def apply_setup(plan: SetupPlan) -> tuple[Path, ...]:
    """Apply a previously built plan and return the targets that changed."""
    changed_paths: list[Path] = []
    for change in plan.changes:
        if not change.changed:
            continue
        change.path.parent.mkdir(parents=True, exist_ok=True)
        if change.content_changed:
            if change.backup and change.path.exists():
                shutil.copy2(change.path, change.path.with_name(change.path.name + ".bak-burnless"))
            change.path.write_bytes(change.after)
        if change.executable:
            change.path.chmod(
                change.path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
        changed_paths.append(change.path)
    return tuple(changed_paths)
