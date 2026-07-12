"""Structural markers for the Living Memory format.

Single source of truth for section names and exchange markers.
Canonical internal keys are the Portuguese names (all downstream code
keys on them); English aliases are accepted on read (dual-read).
"""

# (canonical_pt, english) pairs. Contracts and Refs are identical in both languages.
SECTION_PAIRS: tuple[tuple[str, str], ...] = (
    ("Foco atual", "Current focus"),
    ("Threads abertas", "Open threads"),
    ("Decisões", "Decisions"),
    ("Contracts", "Contracts"),
    ("Refs", "Refs"),
    ("Riscos", "Risks"),
    ("Última validação", "Last validation"),
    ("Recuperáveis", "Recoverables"),
)

SECTION_EN_TO_PT: dict[str, str] = {en: pt for pt, en in SECTION_PAIRS if en != pt}

# Exchange block markers (question/answer). PT variants exist on disk today;
# EN short forms are the v2 format (write-side lands in B2).
EXCHANGE_Q_MARKERS: tuple[str, ...] = ("PERGUNTA:", "Pergunta:", "Q:")
EXCHANGE_A_MARKERS: tuple[str, ...] = ("RESPOSTA:", "Resposta:", "A:")

# Exact standalone lines that mark a raw chat transcript (used by validators
# to reject candidates that contain un-consolidated exchanges).
EXCHANGE_MARKER_LINES: tuple[str, ...] = ("PERGUNTA:", "RESPOSTA:", "Q:", "A:")


def normalize_section(name: str) -> str:
    """Map an English section header to its canonical Portuguese key.

    Portuguese and unknown names pass through unchanged.
    """
    return SECTION_EN_TO_PT.get(name, name)


def find_line_anchored(text: str, marker: str) -> int:
    """Index of marker at start of text or start of a line; -1 if absent.

    Line-anchored matching prevents short markers like "Q:" from matching
    inside prose (e.g. "FAQ:").
    """
    if text.startswith(marker):
        return 0
    idx = text.find("\n" + marker)
    return idx + 1 if idx != -1 else -1
