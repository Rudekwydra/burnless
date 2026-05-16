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
    return f"""<!-- burnless:start -->
<!-- burnless version: v{version} -->
## Burnless — orchestration active in this project

This project has a `.burnless/` directory. Use burnless to delegate work
instead of editing files directly when possible.

### Core commands

- `burnless do "TASK" --tier <bronze|silver|gold>` — delegate + run atomically
- `burnless route "TASK"` — preview which tier/agent would handle a task
- `burnless metrics` — see token savings + counters
- `burnless status` — project health

### Tier selection (spec quality determines tier)

| Signal in spec | Tier |
|---|---|
| Exact files + schema + bugs already diagnosed → mechanical | bronze |
| Implementation with some judgment calls | silver |
| Architecture, structural refactor | gold |
| Irreversible decision / second opinion | diamond |

If you wrote a spec detailed enough to compile mentally into code, it's bronze.
If it requires "thinking through the problem", silver. If it requires
"deciding between architectures", gold.

### Workflow rules

1. **Commit working tree before delegating** — workers may reset files
2. **Audit DoD point-by-point** after worker returns OK — don't trust
   status=OK blindly; grep/test each declared deliverable
3. **PART output → reject and re-spec smaller**, don't merge partial work

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
