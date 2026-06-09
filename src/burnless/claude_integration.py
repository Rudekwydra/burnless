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
    """Emit a SHORT POINTER to the canonical doctrine.

    Doctrine is no longer inlined per-project (that forked stale copies into
    every CLAUDE.md). The single source of truth lives in the burnless repo at
    `docs/DOCTRINE.md` (how it works) and `docs/COMMANDS.md` (verified commands).
    """
    return f"""<!-- burnless:start -->
<!-- burnless version: v{version} -->
## Burnless — orchestration active in this project

This project has a `.burnless/` directory. Delegate work with `burnless do`
instead of editing files directly when possible.

Doctrine is canonical in the burnless repo (do not inline/copy it here):
- **How it works / how to use it:** `docs/DOCTRINE.md`
- **Verified command reference:** `docs/COMMANDS.md`

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
