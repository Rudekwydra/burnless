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


def _token_set(core: str) -> set[str]:
    """Tokenize core into words (split on non-alphanumeric, discard len<2)."""
    tokens = re.split(r'[^a-z0-9]+', core)
    return {t for t in tokens if len(t) >= 2}


def _is_supported_fuzzy(cand_core: str, floor_cores: list[str], threshold: float = 0.6, max_new_ratio: float = 0.4) -> bool:
    """Check if candidate's token containment in any floor line >= threshold.

    Additionally, enforce that new tokens (in candidate but not in any floor line)
    comprise at most max_new_ratio of the candidate's token count.
    This blocks substantial invented tails while allowing faithful micro-edits.
    """
    cand_tokens = _token_set(cand_core)
    if not cand_tokens:
        return True  # Cores with no tokens are supported

    # Union of all tokens across all floor lines
    floor_union = set()
    for floor_core in floor_cores:
        floor_tokens = _token_set(floor_core)
        floor_union.update(floor_tokens)

    # Calculate new tokens (in candidate but not in any floor line)
    new_tokens = cand_tokens - floor_union
    new_ratio = len(new_tokens) / max(1, len(cand_tokens))

    # If too many new tokens, reject immediately
    if new_ratio > max_new_ratio:
        return False

    # Check containment threshold against any floor line
    for floor_core in floor_cores:
        floor_tokens = _token_set(floor_core)
        if not floor_tokens:
            continue  # Skip floor cores with no tokens
        intersection = cand_tokens & floor_tokens
        containment = len(intersection) / max(1, len(cand_tokens))
        if containment >= threshold:
            return True

    return False


def validate_owner_output(
    floor_md: str, candidate_md: str, min_core_len: int = 4
) -> str:
    """
    Valida linha-a-linha. Mantém linhas suportadas, dropa não-suportadas.
    Remove headers órfãos. Se resultado <25% conteúdo do floor ou vazio, retorna floor.
    Nunca levanta exceção — em erro, retorna floor.
    """
    try:
        if not isinstance(floor_md, str):
            return floor_md
        if not isinstance(candidate_md, str) or not candidate_md.strip():
            return floor_md

        # Constrói lista de suporte e conta linhas de conteúdo do floor
        floor_lines = floor_md.split("\n")
        floor_cores = [c for c in (_normalize_core(line) for line in floor_lines) if c]
        floor_content_count = sum(
            1 for line in floor_lines
            if len(_normalize_core(line)) >= min_core_len
        )

        # Filtra candidato linha-a-linha
        candidate_lines = candidate_md.split("\n")
        filtered = []
        for line in candidate_lines:
            core = _normalize_core(line)
            # Headers, vazias ou suportadas: mantém
            if core == "" or _is_supported_fuzzy(core, floor_cores):
                filtered.append(line)
            # Senão: dropa linha

        # Agrupa em seções (header + conteúdo) e remove seções sem conteúdo
        result = []
        i = 0
        while i < len(filtered):
            line = filtered[i]
            if line.startswith("##"):
                # Coleta header + seu conteúdo até próximo header
                section = [line]
                j = i + 1
                while j < len(filtered) and not filtered[j].startswith("##"):
                    section.append(filtered[j])
                    j += 1
                # Verifica se seção tem conteúdo
                has_content = any(
                    len(_normalize_core(l)) >= min_core_len for l in section
                )
                if has_content:
                    result.extend(section)
                i = j
            else:
                # Não-header fora de seção (antes do primeiro header)
                result.append(line)
                i += 1

        # Remove linhas vazias no início/fim
        while result and not result[0].strip():
            result.pop(0)
        while result and not result[-1].strip():
            result.pop()

        result_md = "\n".join(result)

        # Guarda de degeneração
        candidate_content_count = sum(
            1 for line in result
            if len(_normalize_core(line)) >= min_core_len
        )

        # Se floor vazio, retorna resultado
        if floor_content_count == 0:
            return result_md
        # Se candidato vazio ou <25% do floor, fallback
        if candidate_content_count == 0 or candidate_content_count < floor_content_count * 0.25:
            return floor_md

        return result_md
    except Exception:
        return floor_md
