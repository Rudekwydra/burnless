from __future__ import annotations
from pathlib import Path
import sys


_QTP_F_FIXED_SUFFIX = (
    "\n## Output contract\n\n"
    "Worker emits a JSON block with: status (OK|PART|ERR|BLK), kind "
    "(execution|thought), summary, files_touched (absolute or cwd-relative paths), "
    "validated (\"name N bytes\" entries optional), evidence (concrete commands/checks), "
    "issues (list), next.\n"
)

_TELEGRAPHIC_OUTPUT_HINT = (
    "\n## Output style — telegraphic\n\n"
    "Responda em estilo telegráfico: sem fillers, sem prosa expansiva, abreviações curtas.\n"
    "Abreviações comuns: imp=implementar, val=validar, cfg=configuração, doc=documentação, "
    "auth=autenticação, repo=repositório, dir=diretório, arq=arquivo, ||=em paralelo.\n\n"
    "Estrutura obrigatória da saída textual (separada do JSON envelope):\n"
    "1. Header em uma linha: `<tier> :: <status> <action> <files/refs>` (status: OK|PART|ERR|BLK)\n"
    "2. Evidence — comandos rodados + outputs LITERAIS (NUNCA abreviar evidence)\n"
    "3. Relatório breve (1-2 parágrafos): decisões não óbvias, gaps detectados\n\n"
    "Regra dura: evidence, file_paths, command outputs e validated NUNCA são telegrafados — "
    "auditor precisa do literal. Só a prosa narrativa do relatório é telegráfica.\n"
    "O JSON envelope (status, kind, summary, files_touched, validated, evidence, issues, next) "
    "permanece obrigatório.\n"
)


def _build_cacheable_runtime_prefix(project_root: Path, burnless_root: Path) -> str:
    """QTP-F: stable prefix that doesn't change between sibling delegations.

    Putting fixed context BEFORE the variable task description maximizes
    prompt-cache hit rate (Anthropic ephemeral_1h TTL). Subsequent
    delegations in the same project share this prefix verbatim.
    """
    memory_index = burnless_root / "memories" / "index.json"
    memory_hint = (
        f"- Burnless memory index: {memory_index}\n"
        if memory_index.exists()
        else (
            "- Burnless memory index: not created yet. If the task asks about "
            "memory/anotacoes, search common local AI memory folders when your "
            "tools allow it: ~/.claude/projects, ~/.claude/memory, ~/.codex, "
            "~/.config/claude, ~/Documents/AI, ~/Documents/notes, ~/notes.\n"
        )
    )
    return (
        "## Burnless Runtime Context\n\n"
        f"- Working directory for this Worker: {project_root}\n"
        f"- Burnless state directory: {burnless_root}\n"
        f"{memory_hint}"
        "- If the task includes an absolute or relative path, inspect that path directly.\n"
        "- If the task asks to find a repository and no path is provided, search likely "
        "project roots under the working directory, ~/antigravity, ~/projects, and ~/Projects "
        "before returning BLK.\n"
        "- Do not return BLK solely because the original user phrased the request conversationally; "
        "use the available CLI/filesystem tools first.\n"
    )


def _with_runtime_context(
    prompt: str,
    *,
    project_root: Path,
    burnless_root: Path,
    chain: list[str] | None = None,
    cache_prefix: bool | None = None,
) -> str:
    """Compose worker prompt with runtime context.

    QTP-F: when cache_prefix=True (or config.cache_prefix.enabled),
    runtime context goes BEFORE the task (cacheable prefix structure).
    When False (default for backwards compat), context goes after the
    task as in v0.7.0 and earlier.
    """
    runtime = _build_cacheable_runtime_prefix(project_root, burnless_root)

    chain_manifest = ""
    if chain:
        valid: list[str] = []
        for did in chain:
            cap_path = burnless_root / "capsules" / f"{did}.json"
            if cap_path.exists():
                valid.append(did)
            else:
                print(
                    f"[lazy manifest] capsule {did} not found at {cap_path}, omitting",
                    file=sys.stderr,
                )
        if valid:
            lines = ["## Lazy Context Manifest", "- Capsules disponíveis (chain):"]
            for i, did in enumerate(valid):
                label = "predecessor direto" if i == 0 else "irmão"
                lines.append(f"  - .burnless/capsules/{did}.json — {label}")
            lines.append("- Delegations referenciadas:")
            lines.append(f"  - .burnless/delegations/{valid[0]}.md")
            lines.append("- Para ler: use sua tool de leitura (Read/cat). Tudo está no cwd.")
            chain_manifest = "\n".join(lines) + "\n"

    if cache_prefix:
        # QTP-F layout: [FIXED PREFIX] [TASK delta] [chain manifest] [FIXED SUFFIX]
        parts = [runtime.rstrip(), "", prompt.rstrip()]
        if chain_manifest:
            parts.extend(["", chain_manifest.rstrip()])
        parts.extend(["", _QTP_F_FIXED_SUFFIX.rstrip(), _TELEGRAPHIC_OUTPUT_HINT.rstrip(), ""])
        return "\n".join(parts)

    # Legacy layout (pre-QTP-F): task first, runtime context after
    result = f"{prompt.rstrip()}\n\n{runtime}"
    if chain_manifest:
        result = result.rstrip() + "\n" + chain_manifest
    result = result.rstrip() + "\n" + _TELEGRAPHIC_OUTPUT_HINT
    return result
