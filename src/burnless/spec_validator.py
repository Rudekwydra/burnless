"""Deterministic guard against relative project-file paths in delegation specs.

Workers execute in an isolated cwd (~/.burnless/iso-cwd/<uuid>/), so a spec that
says Read("src/burnless/cli.py") resolves the path against the wrong directory
and silently fails (phantom edit). This module flags relative source-file paths
so cmd_delegate can reject the spec before a worker is launched.
"""

from __future__ import annotations

import dataclasses
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


_EXEC_LANGS: frozenset[str] = frozenset(
    {"sh", "bash", "shell", "zsh", "console", "shell-session", "sh-session"}
)


def _executed_regions(text: str) -> str:
    """Text a worker/runner actually executes: fenced shell code blocks plus the
    ## Verify commands. Relative paths here are hard-blocked; prose is only warned."""
    out: list[str] = []
    in_fence = False
    fence_char = ""
    lang = ""
    buf: list[str] = []
    for line in text.splitlines():
        s = line.lstrip()
        if not in_fence:
            if s.startswith("```") or s.startswith("~~~"):
                in_fence = True
                fence_char = s[0]
                info = s.lstrip("`~").strip()
                lang = info.split()[0].lower() if info else ""
                buf = []
            continue
        if s.startswith(fence_char * 3):
            if lang in _EXEC_LANGS:
                out.append("\n".join(buf))
            in_fence = False
            lang = ""
            buf = []
            continue
        buf.append(line)
    try:
        from .delegation_parse import extract_verify_block

        out.extend(extract_verify_block(text))
    except Exception:
        pass
    return "\n".join(out)


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


def format_prose_path_warning(prose_paths: list[str], lang: str = "pt-BR") -> str:
    bullets = "\n".join(f"    {p}" for p in prose_paths)
    if lang.startswith("pt"):
        return (
            "\n[WARN] burnless: caminhos relativos SO na prosa (fora de fences shell e ## Verify) -- nao bloqueiam.\n"
            "   Se algum for alvo de acao do worker, torne-o absoluto; se for so referencia, ignore.\n"
            f"{bullets}\n"
        )
    return (
        "\n[WARN] burnless: relative paths in PROSE only (outside shell fences and ## Verify) -- not blocking.\n"
        "   If any is a worker action target, make it absolute; if it is only a reference, ignore.\n"
        f"{bullets}\n"
    )


_CMD_SUBSTITUTION_RE = re.compile(r"`|\$\(")


def find_verify_command_substitution(text: str) -> list[str]:
    """Lines inside the fenced ## Verify block that contain a literal backtick
    or $(...) — command-substitution that breaks /bin/sh execution unpredictably
    (known reincident footgun). Returns the offending lines verbatim."""
    from .delegation_parse import extract_verify_block
    cmds = extract_verify_block(text)
    return [c for c in cmds if _CMD_SUBSTITUTION_RE.search(c)]


def should_block_verify_command_substitution(text: str) -> bool:
    """True when dispatch must be blocked: the ## Verify block has a line with
    a backtick or $(...) — always blocks, no config toggle (this is never
    intentional in a Verify line; real command-substitution needs a script file)."""
    return bool(find_verify_command_substitution(text))


def format_command_substitution_rejection(offending: list[str], lang: str = "pt-BR") -> str:
    bullets = "\n".join(f"    {line}" for line in offending)
    if lang.startswith("pt"):
        return (
            "\n[BLOCK] burnless: bloco ## Verify usa backtick ou $(...) (command-substitution).\n"
            "   O runner executa cada linha via /bin/sh -c e isso quebra de forma imprevisivel.\n"
            "   Linhas problematicas:\n"
            f"{bullets}\n"
            "   Use greps de 1 linha, sem command-substitution. Logica pesada vai pra um script\n"
            "   .py chamado por uma unica linha do Verify (ex: python3 script.py).\n"
        )
    return (
        "\n[BLOCK] burnless: ## Verify block uses a backtick or $(...) (command substitution).\n"
        "   The runner executes each line via /bin/sh -c and this breaks unpredictably.\n"
        "   Offending lines:\n"
        f"{bullets}\n"
        "   Use single-line greps, no command substitution. Move heavy logic into a .py\n"
        "   script invoked by a single Verify line (e.g. python3 script.py).\n"
    )


@dataclasses.dataclass
class SpecGateResult:
    ok: bool
    text: str
    reason: str = ""
    message: str = ""
    autofix_notice: str = ""


def evaluate_spec_gates(
    text: str,
    cfg: dict,
    project_root,
    *,
    allow_relative_paths: bool = False,
    allow_unfenced_verify: bool = False
) -> SpecGateResult:
    """Centralized spec-gate evaluation reproducing CLI sequence exactly.

    Gates (in order):
    1. Relative paths: validate, try autofix, fail if unfixable
    2. Unfenced verify: block if ## Verify present but no fenced block
    3. Command substitution: block if ## Verify has backtick or $(...)

    Returns SpecGateResult with ok=True if all gates pass, False if blocked.
    When ok=True and autofix was applied, autofix_notice is set.
    When ok=False, reason and message identify which gate failed.
    """
    autofix_notice = ""

    # (a) Relative paths gate — hard-block only inside executed regions
    #     (fenced shell blocks + ## Verify); prose-only hits downgrade to a warning.
    if not allow_relative_paths and cfg.get("validation", {}).get("require_absolute_paths", True):
        lang = cfg.get("language", "pt-BR")
        sv_exec = validate_spec_paths(_executed_regions(text))
        if not sv_exec.ok:
            fixed_text, rewritten = autofix_relative_paths(text, project_root)
            if rewritten and validate_spec_paths(_executed_regions(fixed_text)).ok:
                text = fixed_text
                autofix_notice = format_autofix_notice(rewritten, project_root, lang)
            else:
                return SpecGateResult(
                    ok=False,
                    text=text,
                    reason="relative_paths",
                    message=format_rejection(sv_exec, project_root, lang)
                )
        else:
            prose_only = [
                p for p in validate_spec_paths(text).offending
                if p not in set(sv_exec.offending)
            ]
            if prose_only:
                autofix_notice += format_prose_path_warning(prose_only, lang)

    # (b) Unfenced verify gate
    _enforce_fence = cfg.get("validation", {}).get("enforce_verify_fence", True)
    if should_block_unfenced_verify(text, _enforce_fence, allow_unfenced_verify):
        lang = cfg.get("language", "pt-BR")
        return SpecGateResult(
            ok=False,
            text=text,
            reason="unfenced_verify",
            message=format_verify_warning(lang),
            autofix_notice=autofix_notice
        )

    # (c) Command substitution gate
    _cmd_subst_offending = find_verify_command_substitution(text)
    if _cmd_subst_offending:
        lang = cfg.get("language", "pt-BR")
        return SpecGateResult(
            ok=False,
            text=text,
            reason="verify_command_substitution",
            message=format_command_substitution_rejection(_cmd_subst_offending, lang),
            autofix_notice=autofix_notice
        )

    return SpecGateResult(ok=True, text=text, autofix_notice=autofix_notice)
