"""Deterministic guard against relative project-file paths in delegation specs.

Workers execute in an isolated cwd (~/.burnless/iso-cwd/<uuid>/), so a spec that
says Read("src/burnless/cli.py") resolves the path against the wrong directory
and silently fails (phantom edit). This module flags relative source-file paths
so cmd_delegate can reject the spec before a worker is launched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_EXTS: tuple[str, ...] = (
    "py", "pyi", "ts", "tsx", "js", "jsx", "mjs", "cjs", "go", "rs", "sh",
    "sql", "prisma", "graphql", "gql", "proto", "vue", "svelte", "rb", "java",
    "kt", "swift", "c", "cc", "cpp", "h", "hpp", "css", "scss", "sass", "less",
    "html", "json", "yaml", "yml", "toml", "ini", "cfg", "md", "rst", "txt",
)

_PATH_RE = re.compile(
    r"(?<![\w/.~\-])"
    r"(\.{1,2}/)?"
    r"((?:[\w.\-]+/)+[\w.\-]+\.(?:" + "|".join(SOURCE_EXTS) + r"))"
    r"\b"
)


@dataclass
class SpecValidation:
    ok: bool
    offending: list[str] = field(default_factory=list)


def validate_spec_paths(text: str) -> SpecValidation:
    seen: set[str] = set()
    offending: list[str] = []
    for m in _PATH_RE.finditer(text):
        core = m.group(2)
        if core in seen:
            continue
        seen.add(core)
        offending.append(core)
    return SpecValidation(ok=not offending, offending=offending)


def format_rejection(v: SpecValidation, project_root: Path, lang: str = "pt-BR") -> str:
    root = str(project_root).rstrip("/")
    bullets = "\n".join(f"    {p}  ->  {root}/{p}" for p in v.offending)
    if lang.startswith("pt"):
        return (
            "\n[BLOCK] burnless: spec usa caminhos relativos a arquivos de projeto.\n"
            "   Workers rodam em cwd isolado -- caminhos relativos falham silenciosamente.\n"
            "   Reescreva como absolutos:\n"
            f"{bullets}\n"
            "   Override: --allow-relative-paths  ou  validation.require_absolute_paths: false\n"
        )
    return (
        "\n[BLOCK] burnless: spec references project files by relative path.\n"
        "   Workers run in an isolated cwd -- relative paths fail silently.\n"
        "   Rewrite as absolute:\n"
        f"{bullets}\n"
        "   Override: --allow-relative-paths  or  validation.require_absolute_paths: false\n"
    )
