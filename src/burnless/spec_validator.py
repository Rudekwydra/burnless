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

_VERIFY_SECTION_RE = re.compile(r'^##+\s*(?:Verify|Validation)\b', re.IGNORECASE | re.MULTILINE)
_VALIDATION_ALIAS_RE = re.compile(r'^##+\s*Validation\b', re.IGNORECASE | re.MULTILINE)


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
        # Not an offense if the SAME path also appears in absolute/rooted form
        # somewhere in the spec — the relative hit is just a prose echo of a
        # path the worker was already given absolutely.
        if re.search(r"(?<![.\w/~])[/~][\w./~\-]*" + re.escape(core), text):
            continue
        offending.append(core)
    return SpecValidation(ok=not offending, offending=offending)


def autofix_relative_paths(text: str, project_root: Path) -> tuple[str, list[str]]:
    """Rewrite offending relative project-file paths to their absolute form.

    Uses the same detection as validate_spec_paths, so a path only gets
    rewritten if it would otherwise have been rejected (an existing absolute
    echo elsewhere in the spec is left alone, same as before). Returns
    (fixed_text, rewritten) where `rewritten` lists the relative paths that
    were replaced, for a non-blocking notice to the caller.
    """
    v = validate_spec_paths(text)
    if not v.offending:
        return text, []
    root = str(project_root).rstrip("/")
    fixed = text
    for rel in v.offending:
        pattern = re.compile(r"(?<![\w/.~\-])" + re.escape(rel))
        fixed = pattern.sub(root + "/" + rel, fixed)
    return fixed, v.offending


def format_autofix_notice(rewritten: list[str], project_root: Path, lang: str = "pt-BR") -> str:
    root = str(project_root).rstrip("/")
    bullets = "\n".join(f"    {p}  ->  {root}/{p}" for p in rewritten)
    if lang.startswith("pt"):
        return (
            "\n[AUTOFIX] burnless: caminhos relativos a arquivos de projeto reescritos como absolutos:\n"
            f"{bullets}\n"
            "   Override: --allow-relative-paths  ou  validation.require_absolute_paths: false\n"
        )
    return (
        "\n[AUTOFIX] burnless: relative project-file paths rewritten as absolute:\n"
        f"{bullets}\n"
        "   Override: --allow-relative-paths  or  validation.require_absolute_paths: false\n"
    )


def verify_block_is_silent_noop(text: str) -> bool:
    """True when a ## Verify section is present but yields no fenced commands,
    so the honest-exit-code gate will silently no-op (footgun)."""
    if not _VERIFY_SECTION_RE.search(text):
        return False
    from .delegation_parse import extract_verify_block
    return not extract_verify_block(text)


def should_block_unfenced_verify(text: str, enforce: bool, allow_override: bool) -> bool:
    """True when dispatch must be blocked: a ## Verify section is present but yields
    no fenced commands (silent gate no-op), enforcement is on, and no explicit override."""
    if not enforce or allow_override:
        return False
    return verify_block_is_silent_noop(text)


def format_verify_warning(lang: str = "pt-BR") -> str:
    if lang.startswith("pt"):
        return (
            "\n[WARN] burnless: secao ## Verify presente mas SEM bloco de codigo cercado (```).\n"
            "   extract_verify_block retorna [] -> o gate honest-exit-code NAO vai rodar.\n"
            "   Cerque os comandos de verificacao em ```sh ... ``` para ativar o gate.\n"
        )
    return (
        "\n[WARN] burnless: ## Verify section present but NO fenced code block (```).\n"
        "   extract_verify_block returns [] -> the honest-exit-code gate will NOT run.\n"
        "   Wrap the verify commands in ```sh ... ``` to enable the gate.\n"
    )


def uses_deprecated_validation_heading(text: str) -> bool:
    """True when the spec uses the deprecated `## Validation` heading instead of
    the canonical `## Verify`. Drives a one-time deprecation warning at dispatch."""
    return bool(_VALIDATION_ALIAS_RE.search(text))


def format_validation_alias_warning(lang: str = "pt-BR") -> str:
    if lang.startswith("pt"):
        return (
            "\n[WARN] burnless: secao ## Validation aceita como alias DEPRECADO de ## Verify.\n"
            "   Use ## Verify nas proximas specs e templates; o alias sai numa release futura.\n"
        )
    return (
        "\n[WARN] burnless: ## Validation section accepted as a DEPRECATED alias of ## Verify.\n"
        "   Use ## Verify in future specs and templates; the alias will be removed in a later release.\n"
    )


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
