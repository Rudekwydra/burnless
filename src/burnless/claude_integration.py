"""Idempotent CLAUDE.md block writer.

Writes a Burnless instructions block delimited by HTML-comment markers so
future versions can upgrade the block in-place without touching user content.
"""
from __future__ import annotations
import re
from pathlib import Path

BLOCK_START = "<!-- burnless:start -->"
BLOCK_END = "<!-- burnless:end -->"
BLOCK_PATTERN = re.compile(
    r"<!-- burnless:start[^>]*-->.*?<!-- burnless:end -->",
    re.DOTALL,
)


def render_block(version: str, project_name: str) -> str:
    """Emit a SHORT, self-contained Burnless policy block.

    Self-contained on purpose: a project that runs `burnless init` may not have
    the burnless repo docs available locally, so the block points at the
    installed CLI (`burnless --help`, the `/burnless` menu) and the local
    `.burnless/` state instead of repo-relative doc paths that may be absent.
    """
    return f"""<!-- burnless:start -->
<!-- burnless version: v{version} -->
## Burnless — orchestration active in this project

This project has a `.burnless/` directory. Prefer delegating work with
`burnless do "<spec>"` (atomic) over editing files directly; use
`burnless delegate "<spec>"` then `burnless run <id>` for staged execution.

- **Engagement modes:** `off` (raw chat), `observe` (measure/explain, no
  constraints), `on` (delegate-only + rolling memory + retrieval hints). Switch
  in-session with `/burnless on|observe|off`; `/burnless menu` shows the
  tier/provider table.
- **Worker specs must use absolute paths** (for example `/Users/.../file.py`);
  relative paths fail in the worker's isolated working directory.
- **Recovery:** after `/clear` or a new session, Burnless restores working
  state from its own session memory — no manual raw-log replay.
- **Reference:** run `burnless --help` for current commands and flags.

Project: {project_name}
<!-- burnless:end -->"""


def write_or_update(claude_md_path: Path, version: str, project_name: str) -> str:
    """Write the burnless block to CLAUDE.md.

    Returns one of: "created", "updated", "appended".
    - "created": file did not exist; created with block as sole content (after H1)
    - "updated": file had existing burnless block; replaced in place
    - "appended": file existed without block; appended at end
    """
    block = render_block(version, project_name)

    if not claude_md_path.exists():
        header = f"# {project_name}\n\n"
        claude_md_path.write_text(header + block + "\n", encoding="utf-8")
        return "created"

    existing = claude_md_path.read_text(encoding="utf-8")
    if BLOCK_PATTERN.search(existing):
        new_content = BLOCK_PATTERN.sub(block, existing)
        claude_md_path.write_text(new_content, encoding="utf-8")
        return "updated"

    separator = "\n\n" if existing.endswith("\n") else "\n\n"
    claude_md_path.write_text(existing + separator + block + "\n", encoding="utf-8")
    return "appended"


def remove_block(claude_md_path: Path) -> bool:
    """Remove the burnless block from CLAUDE.md. Returns True if a block was removed."""
    if not claude_md_path.exists():
        return False
    existing = claude_md_path.read_text(encoding="utf-8")
    if not BLOCK_PATTERN.search(existing):
        return False
    new_content = BLOCK_PATTERN.sub("", existing).rstrip() + "\n"
    claude_md_path.write_text(new_content, encoding="utf-8")
    return True
