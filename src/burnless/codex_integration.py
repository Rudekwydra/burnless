"""Idempotent ~/.codex/AGENTS.md block writer.

Structural sibling of `claude_integration.py`. Writes a Burnless instructions
block delimited by HTML-comment markers so future versions can upgrade the
block in-place without touching user content. Unlike CLAUDE.md (per-project),
AGENTS.md for Codex is a HOME-level, global file — there is no project name
to embed, no synthetic H1 header on create.
"""
from __future__ import annotations
import re
from pathlib import Path

BLOCK_START = "<!-- burnless:codex:start -->"
BLOCK_END = "<!-- burnless:codex:end -->"
BLOCK_PATTERN = re.compile(
    r"<!-- burnless:codex:start[^>]*-->.*?<!-- burnless:codex:end -->",
    re.DOTALL,
)


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
- **Recovery:** Codex hooks under `templates/codex/hooks/` already wire
  epoch-based rolling memory via the `SessionStart` hook — no manual raw-log
  replay needed after a new session starts.
- **Reference:** run `burnless --help` for current commands and flags.
<!-- burnless:codex:end -->"""


def write_or_update(agents_md_path: Path, version: str) -> str:
    """Write the burnless block to AGENTS.md.

    Returns one of: "created", "updated", "appended".
    - "created": file did not exist; created with block as sole content
    - "updated": file had existing burnless block; replaced in place
    - "appended": file existed without block; appended at end
    """
    block = render_block(version)

    if not agents_md_path.exists():
        agents_md_path.parent.mkdir(parents=True, exist_ok=True)
        agents_md_path.write_text(block + "\n", encoding="utf-8")
        return "created"

    existing = agents_md_path.read_text(encoding="utf-8")
    if BLOCK_PATTERN.search(existing):
        new_content = BLOCK_PATTERN.sub(block, existing)
        agents_md_path.write_text(new_content, encoding="utf-8")
        return "updated"

    separator = "\n\n" if existing.endswith("\n") else "\n\n"
    agents_md_path.write_text(existing + separator + block + "\n", encoding="utf-8")
    return "appended"


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
