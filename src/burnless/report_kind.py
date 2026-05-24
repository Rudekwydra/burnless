"""Report-kind classification for delegation outputs.

Determines whether a worker task is 'thought' (planning/analysis/design)
or 'execution' (implementation/edit/run) based on keyword hints in the
task text or normalized from an explicit kind field.

Lives in core (not _pro) because it's used by free-tier delegation flow.
"""
from __future__ import annotations


_THOUGHT_HINTS = (
    "planeje", "plano", "plan", "design", "desenhe", "arquitetura", "architecture",
    "decida", "decidir", "decision", "analise", "análise", "analyze", "review",
    "investigue", "investigar", "inspect", "study", "estude", "spec", "brief",
    "proposta", "proposal", "brainstorm", "ideia", "idea",
)
_EXECUTION_HINTS = (
    "implemente", "implementar", "fix", "corrija", "corrigir", "patch", "test",
    "teste", "write", "escreva", "editar", "edit", "create", "criar", "run",
    "execute", "executar", "validate", "validar",
)


def infer_kind_hint(text: str) -> str:
    """Classify text as 'thought' or 'execution' by keyword density.
       Ties and zero-hits default to 'execution' (safer for downstream logic)."""
    low = text.lower()
    thought_score = sum(1 for hint in _THOUGHT_HINTS if hint in low)
    exec_score = sum(1 for hint in _EXECUTION_HINTS if hint in low)
    if thought_score > exec_score:
        return "thought"
    if exec_score > thought_score:
        return "execution"
    return "execution"


def normalize_report_kind(value: object) -> str:
    """Coerce a free-form kind value into canonical 'thought' or 'execution'.
       Unknown values default to 'execution'."""
    text = str(value or "").strip().lower()
    if text in {"thought", "thinking", "design", "plan", "analysis"}:
        return "thought"
    if text in {"execution", "feito", "done", "implemented"}:
        return "execution"
    return "execution"
