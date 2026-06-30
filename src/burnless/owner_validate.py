import re


def _normalize_core(line: str) -> str:
    """
    Remove decorações e retorna núcleo textual minúsculo.
    Ordem: headers/vazias → bullet → trust-tags → provenance → supersede → strip → lower.
    """
    # Headers e linhas vazias
    if line.startswith("##") or not line.strip():
        return ""

    # Bullet inicial
    if line.startswith("- "):
        line = line[2:]

    # Trust-tags no começo ([doctrine], [state], [inflight])
    line = re.sub(r"^\[(doctrine|state|inflight)\]\s*", "", line)

    # Provenance no fim ([chat:...])
    line = re.sub(r"\[chat:[^\]]*\]\s*$", "", line)

    # Supersede (~~...~~ → conteúdo interno)
    line = re.sub(r"~~([^~]*)~~", r"\1", line)

    # Strip e lower
    return line.strip().lower()


def validate_owner_output(
    floor_md: str, candidate_md: str, min_core_len: int = 4
) -> str:
    """
    Valida que cada linha do candidato é suportada pelo floor.
    Se alguma linha não for suportada, retorna floor intacto.
    Nunca levanta exceção — em erro, retorna floor.
    """
    try:
        if not isinstance(floor_md, str) or not isinstance(candidate_md, str):
            return floor_md

        # Constrói blob de suporte (concatenação dos núcleos do floor)
        floor_lines = floor_md.split("\n")
        support_blob = " ".join(_normalize_core(line) for line in floor_lines)

        # Valida cada linha do candidato
        candidate_lines = candidate_md.split("\n")
        for line in candidate_lines:
            core = _normalize_core(line)
            # Ignora linhas muito curtas (headers, vazias, triviais)
            if len(core) < min_core_len:
                continue
            # Núcleo DEVE estar em support_blob
            if core not in support_blob:
                return floor_md  # Falha — retorna floor intacto

        # Tudo OK — retorna candidato
        return candidate_md
    except Exception:
        # Erro interno — fail-closed
        return floor_md
