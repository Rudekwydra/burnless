"""Delegation markdown parsing utilities extracted from cli.py.

These helpers read structured fields from the delegation markdown files
that burnless writes to .burnless/delegations/. They are pure functions
with no external dependencies beyond stdlib (re).

Public API (also re-exported by cli.py with leading-underscore aliases):

    parse_chain_from_delegation(md)        — chain list from YAML frontmatter
    parse_tier_from_delegation(md)         — tier name from agent line
    parse_created_at_from_delegation(md)   — ISO timestamp from header
    parse_goal_from_delegation(md)         — goal text from `## Goal` section
    extract_test_status(summary)           — derive OK/FAIL/SKIP from summary
    extract_verify_block(md)               — shell commands from `## Verify` fenced block
"""
from __future__ import annotations

import re


def parse_chain_from_delegation(md: str) -> list[str]:
    """Parse chain list from YAML front-matter at top of delegation markdown."""
    if not md.startswith("---"):
        return []
    end = md.find("---", 3)
    if end == -1:
        return []
    frontmatter = md[3:end].strip()
    for line in frontmatter.splitlines():
        if line.startswith("chain:"):
            value = line.split(":", 1)[1].strip().strip("[]")
            return [x.strip() for x in value.split(",") if x.strip()]
    return []


def parse_tier_from_delegation(md: str) -> str | None:
    for line in md.splitlines():
        if line.lower().startswith("- **agent:**"):
            # "- **agent:** opus (gold)"
            if "(" in line and ")" in line:
                return line.rsplit("(", 1)[1].split(")", 1)[0].strip()
    return None


def parse_created_at_from_delegation(md: str) -> str | None:
    """Extract created_at ISO timestamp from delegation markdown frontmatter."""
    m = re.search(r"\*\*created_at:\*\*\s*(\S+)", md)
    return m.group(1) if m else None


def parse_goal_from_delegation(md: str) -> str | None:
    if "## Goal" not in md:
        return None
    after = md.split("## Goal", 1)[1]
    end = after.find("##")
    block = after[:end] if end != -1 else after
    text = " ".join(block.split())
    return text or None


_VERIFY_HEADING_RE = re.compile(r'^##+\s*(?:Verify|Validation)\b', re.IGNORECASE)
_SHELL_FENCE_LANGS = ("", "sh", "bash", "shell", "verify")


def extract_verify_block(md: str) -> list[str]:
    """Extract shell commands from the first fenced code block under a ## Verify section.

    Fence-aware (2026-07-20, footgun 17/07): a `## Verify` line INSIDE a fenced
    example in the spec body is ignored — only a heading outside any fence
    starts the section. Within the section, non-shell fences (```md, ```js...)
    are skipped; the first bare/sh/bash/shell/verify fence wins.

    Returns each non-blank, non-comment line as one command string (stripped).
    Returns [] when the section or a matching fenced block is absent.
    """
    lines = md.splitlines()
    in_fence = False
    fence_char = ""

    # 1) Locate the ## Verify heading OUTSIDE any fence.
    section_start = None
    for i, line in enumerate(lines):
        s = line.lstrip()
        if s.startswith("```") or s.startswith("~~~"):
            if not in_fence:
                in_fence, fence_char = True, s[0]
            elif s.startswith(fence_char * 3):
                in_fence = False
            continue
        if not in_fence and _VERIFY_HEADING_RE.match(line):
            section_start = i + 1
            break
    if section_start is None:
        return []

    # 2) Within the section (until the next heading outside a fence), take the
    #    first shell-ish fenced block; skip fences of other languages.
    cmds: list[str] = []
    in_fence = False
    collecting = False
    for line in lines[section_start:]:
        s = line.lstrip()
        if not in_fence:
            if s.startswith("```") or s.startswith("~~~"):
                fence_char = s[0]
                info = s.lstrip("`~").strip()
                lang = info.split()[0].lower() if info else ""
                in_fence = True
                collecting = lang in _SHELL_FENCE_LANGS
                continue
            if line.startswith("##"):
                break
            continue
        if s.startswith(fence_char * 3):
            in_fence = False
            if collecting:
                return [c for c in cmds if c and not c.startswith('#')]
            collecting = False
            continue
        if collecting:
            cmds.append(line.strip())
    return []


from .codec.decoder import _coerce_to_list


def extract_test_status(summary: dict) -> str:
    items = _coerce_to_list(summary.get("validated")) + _coerce_to_list(summary.get("evidence"))
    for item in items:
        text = str(item).lower()
        if "pytest" in text or "passed" in text or "failed" in text:
            m = re.search(r"(\d+)\s+passed", text)
            if m:
                return f"OK:{m.group(1)}"
            m = re.search(r"(\d+)\s+failed", text)
            if m:
                return f"FAIL:{m.group(1)}"
            if "passed" in text:
                return "OK"
            if "failed" in text:
                return "FAIL"
    return "SKIP"
