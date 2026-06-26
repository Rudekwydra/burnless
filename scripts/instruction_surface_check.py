import json
import os
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

SURFACES = [
    ".claude/commands/burnless.md",
    ".claude/commands/burnless-delegate.md",
    ".claude/commands/burnless-plan.md",
    ".claude/commands/burnless-status.md",
    "templates/scripts/burnless_mode_hook.sh",
    "templates/scripts/burnless_epoch_session.sh",
    "templates/scripts/burnless_epoch_stop.sh",
    "templates/scripts/burnless_session_seed.sh",
    "templates/scripts/burnless_offload_hook.sh",
    "templates/hooks/burnless_compact_haiku.sh",
    "templates/agents/burnless-planner.md",
    "templates/agents/burnless-worker.md",
    "templates/delegation_filter.sh",
    "src/burnless/claude_integration.py",
    "src/burnless/init_claude_code.py",
]

ALLOW_MARKERS = [
    "legacy",
    "deprecat",
    "coerce",
    "coerced",
    "back-compat",
    "backward",
    "removed",
    "compat",
    "historical",
]

FORBIDDEN = [
    ("burnless shell", re.compile(r"burnless\s+shell", re.IGNORECASE)),
    ("burnless chat", re.compile(r"burnless\s+chat\b", re.IGNORECASE)),
    ("/chat command", re.compile(r"(?<![\w-])/chat\b(?!-)", re.IGNORECASE)),
    ("/rewind mainline", re.compile(r"(?<![\w-])/rewind\b", re.IGNORECASE)),
    ("--chat flag", re.compile(r"--chat\b(?!-)", re.IGNORECASE)),
    ("epochs.on marker", re.compile(r"epochs\.on\b", re.IGNORECASE)),
    ("brain_streams", re.compile(r"brain_streams", re.IGNORECASE)),
    ("legacy mode partner", re.compile(r"\bpartner\b", re.IGNORECASE)),
    ("legacy mode rollover", re.compile(r"\brollover\b", re.IGNORECASE)),
    ("base64_ciphertext", re.compile(r"base64_ciphertext", re.IGNORECASE)),
    ("burnless decode", re.compile(r"burnless\s+decode\b", re.IGNORECASE)),
    ("burnless compress", re.compile(r"burnless\s+compress\b", re.IGNORECASE)),
    ("rtk wrapper", re.compile(r"\brtk\b", re.IGNORECASE)),
]


def scan_text(text, *, rules=FORBIDDEN, allow_markers=ALLOW_MARKERS):
    """
    Scan text for forbidden patterns. Returns list of finding dicts.
    Lines with allow_markers (case-insensitive) are skipped.
    """
    findings = []
    try:
        lines = text.split("\n")
    except Exception:
        return []

    for line_num, line in enumerate(lines, start=1):
        # Check if line should be allowlisted
        line_lower = line.lower()
        if any(marker.lower() in line_lower for marker in allow_markers):
            continue

        # Check each forbidden pattern
        for rule_name, rule_rx in rules:
            try:
                if rule_rx.search(line):
                    text_excerpt = line.strip()[:160]
                    findings.append({
                        "line": line_num,
                        "rule": rule_name,
                        "text": text_excerpt,
                    })
            except Exception:
                pass

    return findings


def scan_file(path):
    """
    Scan a single file. Returns list of findings or [] on error.
    """
    try:
        content = path.read_text(encoding="utf-8")
        return scan_text(content)
    except Exception:
        return []


def scan_surfaces(repo_root=REPO_ROOT, surfaces=SURFACES):
    """
    Scan all surfaces. Returns dict {rel_path: findings} or {} if clean.
    """
    results = {}
    for rel_path in surfaces:
        try:
            full_path = repo_root / rel_path
            findings = scan_file(full_path)
            if findings:
                results[rel_path] = findings
        except Exception:
            pass

    return results


def render(results):
    """
    Render results dict as human-readable report.
    """
    if not results:
        return "instruction surfaces clean: no forbidden active guidance found"

    lines = []
    for file_path in sorted(results.keys()):
        findings = results[file_path]
        lines.append(f"FILE: {file_path} ({len(findings)} findings)")
        for finding in findings:
            line_num = finding["line"]
            rule = finding["rule"]
            text = finding["text"]
            lines.append(f"  L{line_num} [{rule}] {text}")

    return "\n".join(lines)


def main(argv=None):
    """
    Main entry point. Returns 0 if clean, 1 if findings.
    """
    results = scan_surfaces()
    print(render(results))
    return 0 if not results else 1


if __name__ == "__main__":
    raise SystemExit(main())
