from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

SECTIONS = ["Foco atual", "Threads abertas", "Decisões", "Contracts", "Refs"]

# Living Memory V3 — additive 8-section model (V2 stays intact above)
SECTIONS_V3 = ["Foco atual", "Threads abertas", "Decisões", "Contracts", "Refs", "Riscos", "Última validação", "Recuperáveis"]

_REF_LINE_RE = re.compile(
    r'^(?P<path>/\S+?)(?:#L(?P<l1>\d+)(?:-(?P<l2>\d+))?)?\s+—\s+(?P<why>.+?)\s+\[seq (?P<seq>\d+(?:-\d+)?)\]$'
)
_RECUPERAVEL_LINE_RE = re.compile(
    r'^(?P<did>d\d{2,4})\s+—\s+(?P<why>.+?)\s+\[seq (?P<seq>\d+(?:-\d+)?)\]$'
)

ENCODER_SYSTEM_PROMPT = (
    "Você é o compactador de memória do Burnless. Você recebe um resumo prévio (não confiável, "
    "gerado por máquina) e trocas verbatim (única fonte de verdade). Sua única saída é um "
    "documento markdown de memória. Você NUNCA continua a conversa, NUNCA inventa perguntas, "
    "respostas, testes ou eventos ausentes do input, NUNCA se dirige ao usuário. Se o input não "
    "contiver fatos novos, devolva o documento anterior inalterado."
)

# Entity patterns: absolute paths, delegation ids, commit hashes, file.ext tokens
_ENTITY_PATTERNS = [
    re.compile(r'/[\w][\w./\-]+'),
    re.compile(r'\bd\d{2,4}\b'),
    re.compile(r'\b[0-9a-f]{7,40}\b'),
    re.compile(r'\b[\w\-]+\.[A-Za-z]{1,5}\b'),
]

# No-op patterns: trivial confirmations and short questions
_NOOP_PATTERNS = [
    re.compile(r'^\s*(ok|okay|blz|beleza|valeu|vlw|isso|isso a[ií]|certo|perfeito|show|fechou|fechado|'
               r'pode (ir|fazer|seguir|mandar)|manda|vai( em frente)?|segue|bom|[oó]timo|massa|top|'
               r'sim|n[aã]o|nope|yep|sure|thanks|obrigad[oa])\b', re.IGNORECASE),
]


def _is_trivial_text(s: str) -> bool:
    t = s.strip()
    if not t:
        return True
    if any(p.match(t) for p in _NOOP_PATTERNS) and len(t) <= 200:
        return True
    if t.endswith('?') and len(t) <= 120:
        return True
    return False


def living_path(root, chat_id: str) -> Path:
    from . import epochs
    return epochs.epoch_dir(Path(root), chat_id) / "living.md"


def state_path(root, chat_id: str) -> Path:
    from . import epochs
    return epochs.epoch_dir(Path(root), chat_id) / "state.json"


def ring_dir(root, chat_id: str) -> Path:
    from . import epochs
    return epochs.epoch_dir(Path(root), chat_id) / "ring"


def extract_entities(text: str) -> set[str]:
    if not text:
        return set()
    entities = set()
    for pattern in _ENTITY_PATTERNS:
        for match in pattern.finditer(text):
            entities.add(match.group(0))
    return entities


def contract_key(line: str) -> str:
    line = line.lstrip('- ').strip()
    ents = extract_entities(line)
    return next(iter(ents)) if ents else line


def update_contract_ages(prev_ages: dict | None, new_md: str, turn: int) -> dict:
    ages = dict(prev_ages or {})
    for line in parse_living(new_md).get('Contracts', []):
        ages[contract_key(line)] = turn
    return ages


def is_noop(prev_md: str, exchange: str, max_len: int = 240) -> bool:
    exchange_stripped = exchange.strip()
    if not exchange_stripped:
        return True

    exchange_entities = extract_entities(exchange_stripped)
    prev_entities = extract_entities(prev_md)

    if not exchange_entities.issubset(prev_entities):
        return False

    if len(exchange_stripped) <= max_len:
        return True

    user_portion = exchange_stripped
    for marker in ('PERGUNTA:', 'Pergunta:'):
        if marker in exchange_stripped:
            start = exchange_stripped.find(marker) + len(marker)
            rest = exchange_stripped[start:].strip()
            for end_marker in ('RESPOSTA:', 'Resposta:', '\n\n'):
                if end_marker in rest:
                    end = rest.find(end_marker)
                    user_portion = rest[:end].strip()
                    break
            if user_portion:
                break

    return _is_trivial_text(user_portion)


def parse_living(md: str) -> dict[str, list[str]]:
    result = {section: [] for section in SECTIONS}
    if not md.strip():
        return result

    lines = md.split('\n')
    current_section = None
    current_body = []

    for line in lines:
        if line.startswith('## '):
            if current_section and current_body:
                body_lines = [l.strip() for l in current_body if l.strip()]
                result[current_section] = [l.lstrip('- ').strip() if l.lstrip().startswith('- ') else l for l in body_lines]
            current_section = line[3:].strip()
            current_body = []
        else:
            if current_section:
                current_body.append(line)

    if current_section and current_body:
        body_lines = [l.strip() for l in current_body if l.strip()]
        result[current_section] = [l.lstrip('- ').strip() if l.lstrip().startswith('- ') else l for l in body_lines]

    return result


def _parse_seq_range(seq_raw: str) -> list[int]:
    if '-' in seq_raw:
        a, b = seq_raw.split('-', 1)
        return [int(a), int(b)]
    return [int(seq_raw), int(seq_raw)]


def _parse_ref_line(line: str) -> dict | str:
    m = _REF_LINE_RE.match(line.strip())
    if not m:
        return line
    l1 = m.group('l1')
    l2 = m.group('l2')
    lines = None
    if l1:
        lines = [int(l1), int(l2) if l2 else int(l1)]
    return {
        "path": m.group('path'),
        "lines": lines,
        "why": m.group('why'),
        "seq": _parse_seq_range(m.group('seq')),
    }


def _parse_recuperavel_line(line: str) -> dict | str:
    m = _RECUPERAVEL_LINE_RE.match(line.strip())
    if not m:
        return line
    return {
        "d": m.group('did'),
        "why": m.group('why'),
        "seq": _parse_seq_range(m.group('seq')),
    }


def harvest_state(md: str) -> dict:
    parsed = parse_living_v3(md)
    contracts = [line.lstrip('- ').strip() for line in parsed.get('Contracts', [])]
    open_threads = [line.lstrip('- ').strip() for line in parsed.get('Threads abertas', [])]

    refs = []
    refs_unparsed = 0
    for line in parsed.get('Refs', []):
        parsed_ref = _parse_ref_line(line)
        refs.append(parsed_ref)
        if isinstance(parsed_ref, str):
            refs_unparsed += 1

    recuperaveis = []
    recuperaveis_unparsed = 0
    for line in parsed.get('Recuperáveis', []):
        parsed_rec = _parse_recuperavel_line(line)
        recuperaveis.append(parsed_rec)
        if isinstance(parsed_rec, str):
            recuperaveis_unparsed += 1

    return {
        "contracts": contracts,
        "refs": refs,
        "open_threads": open_threads,
        "recuperaveis": recuperaveis,
        "refs_unparsed": refs_unparsed,
        "recuperaveis_unparsed": recuperaveis_unparsed,
    }


def living_rewrite_prompt(prev_md: str, exchange: str, budget_tokens: int = 2500) -> str:
    prompt = f"""Você é um assistente de memória do Burnless. Sua tarefa é ATUALIZAR um documento vivo em markdown.

## Instrução CRÍTICA
Retorne o documento COMPLETO atualizado com EXATAMENTE 5 seções (nesta ordem):
1. ## Foco atual
2. ## Threads abertas
3. ## Decisões
4. ## Contracts
5. ## Refs

**NUNCA altere strings de Contracts existentes**: caminhos, IDs (d000), hashes, assinaturas. Copie verbatim ou REMOVA a linha inteira.
Mantenha todo o doc sob ~{budget_tokens} tokens; comprima 'Decisões' ao máximo.
Sem pensamento/debate/markdown extra — apenas as 5 seções.

## Documento anterior (se houver)
```
{prev_md if prev_md else ''}
```

## Nova troca/evento
```
{exchange}
```

## Atualização esperada
- Mova pendências resolvidas de 'Threads abertas' → uma linha em 'Decisões'
- Mantenha apenas threads relevantes; descarte resolvidas antigas
- Preserve Contracts verbatim; deixe Refs e Threads evaporarem se irrelevantes
- Comprima 'Decisões': uma linha por decisão, resumida

Retorne apenas o documento markdown atualizado. Sem markdown fence. Pronto."""
    return prompt


def preserve_guard(prev_md: str, new_md: str, contract_ages: dict | None = None, turn: int = 0, max_age: int = 15) -> str:
    prev_parsed = parse_living(prev_md)
    prev_contracts = prev_parsed.get('Contracts', [])

    new_parsed = parse_living(new_md)
    new_contracts = new_parsed.get('Contracts', [])

    recovered = []
    for contract_line in prev_contracts:
        contract_line_clean = contract_line.lstrip('- ').strip()
        first_entity = extract_entities(contract_line_clean)
        if first_entity:
            first_token = next(iter(first_entity))
            if first_token not in new_md:
                key = contract_key(contract_line_clean)
                if contract_ages is None:
                    recovered.append(contract_line_clean)
                else:
                    age_val = contract_ages.get(key, turn)
                    if turn - age_val <= max_age:
                        recovered.append(contract_line_clean)

    if not recovered:
        return new_md

    if '## Contracts' not in new_md:
        new_md += '\n## Contracts\n'

    contracts_section_marker = new_md.find('## Contracts')
    if contracts_section_marker == -1:
        new_md += '\n## Contracts\n'
        for line in recovered:
            new_md += f'- {line}\n'
    else:
        eol = new_md.find('\n', contracts_section_marker)
        if eol == -1:
            new_md += '\n'
            for line in recovered:
                new_md += f'- {line}\n'
        else:
            insert_pos = eol + 1
            next_section = -1
            for section in SECTIONS:
                if section != 'Contracts':
                    marker = new_md.find(f'## {section}', insert_pos)
                    if marker != -1 and (next_section == -1 or marker < next_section):
                        next_section = marker

            if next_section == -1:
                for line in recovered:
                    new_md += f'- {line}\n'
            else:
                recovered_text = '\n'.join(f'- {line}' for line in recovered) + '\n'
                new_md = new_md[:next_section] + recovered_text + new_md[next_section:]

    return new_md


def _exchange_mentions(nucleus: str, exchange: str) -> bool:
    """Deterministic check: does the exchange plausibly reference this thread?

    Transducer semantics: the rewriter may only change entries the current
    exchange touches. A thread may evaporate ONLY if the exchange mentions it —
    measured as >=50% of the nucleus' significant tokens (alnum, len>=4,
    case-insensitive exact match) appearing in the exchange. Zero LLM calls.
    """
    if not exchange:
        return False
    nucleus_tokens = {t for t in re.findall(r"\w+", nucleus.lower()) if len(t) >= 4}
    if not nucleus_tokens:
        return False
    exchange_tokens = {t for t in re.findall(r"\w+", exchange.lower()) if len(t) >= 4}
    hits = len(nucleus_tokens & exchange_tokens)
    return hits * 2 >= len(nucleus_tokens)


def preserve_open_threads(prev_md: str, new_md: str, exchange: str = "") -> str:
    """Prevent open-Thread evaporation during LLM rewrite (P10/4).

    For EVERY thread in prev's 'Threads abertas' section:
    - Extract nucleus (core text) by stripping trust-prefix and trailing tags
    - If nucleus (exact substring) does NOT appear anywhere in new_md, the
      thread evaporated. It is reinjected UNLESS the current exchange mentions
      it (see _exchange_mentions) — a thread the exchange never touched cannot
      be dropped; a thread the exchange discusses may be closed by the rewriter.
    - Threads resolved into 'Decisões' or demoted to 'Recuperáveis' verbatim
      still carry the nucleus, so those paths are never blocked either way.
    - Ignore nuclei < 12 chars (avoid false positives on trivial lines)
    - If new_md lacks '## Threads abertas' section, create it in canonical position (2nd)

    Returns reconstructed doc with evaporated threads preserved.
    """
    prev_parsed = parse_living_v3(prev_md)
    new_parsed = parse_living_v3(new_md)

    prev_threads = prev_parsed.get('Threads abertas', [])
    evaporated = []

    for thread_line in prev_threads:
        thread_line_clean = thread_line.lstrip('- ').strip()
        if not thread_line_clean:
            continue

        nucleus = _TRUST_PREFIX_RE.sub('', thread_line_clean)
        while True:
            m = _TRAILING_TAG_RE.search(nucleus)
            if not m:
                break
            nucleus = nucleus[:m.start()]
        nucleus = nucleus.strip()

        if len(nucleus) < 12:
            continue

        if nucleus not in new_md and not _exchange_mentions(nucleus, exchange):
            evaporated.append(thread_line_clean)

    if not evaporated:
        return new_md

    if '## Threads abertas' not in new_md:
        new_md = _inject_missing_threads_section(new_md, evaporated)
    else:
        threads_section_marker = new_md.find('## Threads abertas')
        if threads_section_marker != -1:
            eol = new_md.find('\n', threads_section_marker)
            if eol != -1:
                insert_pos = eol + 1
                next_section_pos = -1
                for section in SECTIONS_V3:
                    if section != 'Threads abertas':
                        marker = new_md.find(f'## {section}', insert_pos)
                        if marker != -1 and (next_section_pos == -1 or marker < next_section_pos):
                            next_section_pos = marker

                if next_section_pos == -1:
                    for line in evaporated:
                        new_md += f'- {line}\n'
                else:
                    evaporated_text = '\n'.join(f'- {line}' for line in evaporated) + '\n'
                    new_md = new_md[:next_section_pos] + evaporated_text + new_md[next_section_pos:]

    return new_md


def _inject_missing_threads_section(new_md: str, threads: list[str]) -> str:
    """Create '## Threads abertas' section in canonical position (2nd) with given threads."""
    threads_section = '\n## Threads abertas\n'
    for line in threads:
        threads_section += f'- {line}\n'
    threads_section += '\n'

    foco_marker = new_md.find('## Foco atual')
    if foco_marker == -1:
        return new_md + threads_section

    next_section_after_foco = -1
    eol_foco = new_md.find('\n', foco_marker)
    if eol_foco == -1:
        return new_md + threads_section

    for section in SECTIONS_V3:
        if section != 'Foco atual':
            marker = new_md.find(f'## {section}', eol_foco + 1)
            if marker != -1:
                next_section_after_foco = marker
                break

    if next_section_after_foco == -1:
        return new_md + threads_section

    return new_md[:next_section_after_foco] + threads_section + new_md[next_section_after_foco:]


def enforce_budget(md: str, budget_tokens: int = 2500, contract_ages: dict | None = None, turn: int = 0, max_age: int = 15) -> str:
    estimated_tokens = len(md) // 4
    if estimated_tokens <= budget_tokens:
        return md

    parsed = parse_living(md)
    decisoes = parsed.get('Decisões', [])

    while decisoes and (len(md) // 4) > budget_tokens:
        decisoes.pop(0)
        parsed['Decisões'] = decisoes
        md = _rebuild_md(parsed)

    if contract_ages is not None and (len(md) // 4) > budget_tokens:
        contracts = parsed.get('Contracts', [])
        stale_contracts = []
        for line in contracts:
            key = contract_key(line)
            age_val = contract_ages.get(key, turn)
            if turn - age_val > max_age:
                stale_contracts.append((turn - age_val, line))

        stale_contracts.sort(reverse=True)
        for age, line in stale_contracts:
            if (len(md) // 4) <= budget_tokens:
                break
            contracts.remove(line)
            parsed['Contracts'] = contracts
            md = _rebuild_md(parsed)

    return md


def _rebuild_md(parsed: dict[str, list[str]]) -> str:
    lines = []
    for section in SECTIONS:
        lines.append(f'## {section}')
        for body_line in parsed.get(section, []):
            if not body_line.startswith('- '):
                lines.append(f'- {body_line}')
            else:
                lines.append(body_line)
        lines.append('')
    return '\n'.join(lines)


def parse_living_v3(md: str) -> dict[str, list[str]]:
    """Parse a Living Memory doc into the 8-section V3 model.

    Result dict always contains all 8 SECTIONS_V3 keys (empty if missing).
    Accepts a 5-section V2 doc too: the 3 new sections come back empty.
    Unknown ``## X`` headers outside the 8 are still captured as extra keys,
    mirroring parse_living's dynamic behavior.

    Code blocks (lines between ``` markers) are treated as atomic multi-line entries.
    Empty entries and orphan fences are discarded.
    """
    result = {section: [] for section in SECTIONS_V3}
    if not md.strip():
        return result

    ignored_headers = {"Documento completo atualizado"}
    lines = md.split('\n')
    current_section = None
    current_body = []

    for line in lines:
        if line.startswith('## '):
            if current_section and current_body:
                entries = _parse_section_entries(current_body)
                result[current_section] = entries
            current_section = line[3:].strip()
            if current_section in ignored_headers:
                current_section = None
            current_body = []
        else:
            if current_section:
                current_body.append(line)

    if current_section and current_body:
        entries = _parse_section_entries(current_body)
        result[current_section] = entries

    return result


def _parse_section_entries(body_lines: list[str]) -> list[str]:
    """Parse section body into entries, grouping code blocks into atomic units.

    - Lines outside code blocks: one entry per non-empty line (strip, lstrip '- ')
    - Code blocks (``` ... ```): one entry per complete block (multi-line, verbatim)
    - Discard empty entries and orphan fences
    """
    entries = []
    i = 0

    while i < len(body_lines):
        line = body_lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped == '```':
            fence_start = i
            i += 1
            fence_found = False

            while i < len(body_lines):
                if body_lines[i].strip() == '```':
                    fence_found = True
                    i += 1
                    break
                i += 1

            if fence_found:
                block_lines = body_lines[fence_start:i]
                block_text = '\n'.join(block_lines)
                entries.append(block_text)
        else:
            if line.lstrip().startswith('- '):
                entry = line.lstrip('- ').strip()
            else:
                entry = line.strip()

            if entry and entry != "<vazio>":
                entries.append(entry)

            i += 1

    return entries


def _rebuild_md_v3(parsed: dict[str, list[str]]) -> str:
    result_lines = []
    for section in SECTIONS_V3:
        result_lines.append(f'## {section}')
        for entry in parsed.get(section, []):
            if '\n' in entry:
                result_lines.append(entry)
            else:
                if not entry.startswith('- '):
                    result_lines.append(f'- {entry}')
                else:
                    result_lines.append(entry)
        result_lines.append('')
    return '\n'.join(result_lines)


def living_rewrite_prompt_v3(prev_md: str, exchange: str, budget_tokens: int = 2500) -> str:
    prompt = f"""Você é um assistente de memória do Burnless. Sua tarefa é ATUALIZAR um documento vivo em markdown (Living Memory V3).

## Instrução CRÍTICA
Retorne o documento COMPLETO atualizado com EXATAMENTE 8 seções (nesta ordem):
1. ## Foco atual
2. ## Threads abertas
3. ## Decisões
4. ## Contracts
5. ## Refs
6. ## Riscos
7. ## Última validação
8. ## Recuperáveis

**NUNCA altere strings de Contracts existentes**: caminhos, IDs (d000), hashes, assinaturas. Copie verbatim ou REMOVA a linha inteira.
Mantenha todo o doc sob ~{budget_tokens} tokens; comprima 'Decisões' ao máximo.
Sem pensamento/debate/markdown extra — apenas as 8 seções.

## Guia das seções V3
- 'Riscos': riscos abertos / o que pode dar errado, ainda não mitigado.
- 'Última validação': último comando/teste/auditoria verificado e seu status (ex: 'pytest -q OK', 'd724 OK').
- 'Recuperáveis': ids dNNN + dicas de comando para recuperar contexto (NÃO logs crus). Ex: 'd725 — pytest tests/test_epochs_v3.py'.
- Formato de linha em 'Refs': `<path_absoluto>[#Linicio[-fim]] — <why curto> [seq <numero-real-do-seq>]` — troque `<numero-real-do-seq>` pelo número de seq verdadeiro da troca que originou o fato (olhe o `seq NNN` da troca pendente ou do documento anterior). NUNCA copie um número de exemplo — se não souber o seq real, omita o marcador `[seq ...]` inteiro nessa linha.
- Formato de linha em 'Recuperáveis': `dNNN — <dica de comando> [seq <numero-real-do-seq>]` — mesma regra: use o seq real da troca, nunca um placeholder.
- Linha que não seguir o formato ainda é aceita (fallback), mas PREFIRA o formato quando souber o path/seq exatos.

### Regra de PONTEIRO (crítica — a memória aponta, não cola)
- Fato ancorado em arquivo (qualquer path citado na troca, com ou sem linhas) DEVE virar UMA linha em 'Refs' no formato `path#Lx-y — <why curto> [seq N]` — NUNCA um parágrafo em 'Decisões' colando o conteúdo do arquivo.
- 'Decisões' registra apenas a decisão em si, numa linha curta; o detalhe fica no arquivo, apontado pela Ref. O leitor tem a tool Read e lê sob demanda.
- Cole conteúdo no documento SOMENTE quando não existe arquivo-fonte para apontar.
- NÃO duplique: se um fato virou linha de 'Refs', não repita o mesmo conteúdo em 'Decisões' (uma menção curta à decisão pode coexistir com a Ref, mas nunca o conteúdo copiado).

### Eixos de consolidação
- Provenance: toda entrada de 'Threads abertas', 'Decisões' e 'Riscos' termina com marcador de origem `[chat:CURTO·tN]` quando a troca trouxer essa info; se não houver, omita o marcador (não invente).
- Supersede: se a nova troca CONTRADIZ uma decisão existente, NÃO apague — mova a antiga pra 'Recuperáveis' como pointer e registre a nova em 'Decisões'. Recência sozinha NÃO supersede; só contradição explícita.
- Trust-boundary: prefixe entradas com a faixa `[doctrine]`, `[state]` ou `[inflight]`. `[doctrine]` nunca evapora por idade; `[state]` e `[inflight]` podem evaporar.
- Deletion: o que sair por idade/irrelevância vira pointer em 'Recuperáveis' (dNNN + dica de comando), nunca é apagado cru — exceto Refs triviais.
- Slot-routing: roteie cada fato pela SEMÂNTICA — tarefa ainda aberta → 'Threads abertas'; decisão fechada → 'Decisões'; comando/teste verificado → 'Última validação'. Não deixe tarefa aberta cair só em 'Decisões'.
- Evidence-retrieval: 'Recuperáveis' guarda só dNNN + dica de comando + `chat:CURTO·tN`, nunca conteúdo cru.
- Plano-futuro: intenção declarada e ainda não executada ("na volta faço X", "próximo passo Y", "fase N pendente") é Thread ABERTA — entregar o design/spec/commit de uma etapa NÃO fecha a thread da etapa seguinte. 'Foco atual' NUNCA vira "tudo completo" enquanto existir thread aberta; nesse caso 'Foco atual' aponta a próxima ação pendente.

## Regra VERBATIM (crítica)
- Você é um TRANSDUTOR, não um redator. NUNCA reescreva, parafraseie, resuma ou traduza o texto de uma entrada existente.
- Para cada entrada que mantiver: copie o TEXTO-NÚCLEO exatamente como está (mesmas palavras), e só (a) mova-a para a seção semanticamente correta, (b) prefixe a faixa `[doctrine]/[state]/[inflight]`, (c) anexe provenance `[chat:ID·tN]` ou marque supersede. O núcleo entre as decorações deve ser substring exata do original.
- Pode REMOVER entradas (dedup, superadas, irrelevantes) e REORDENAR. NÃO pode inventar frases novas nem juntar duas entradas numa paráfrase.

## Documento anterior (se houver)
```
{prev_md if prev_md else ''}
```

## Nova troca/evento
```
{exchange}
```

## Atualização esperada
- Mova pendências resolvidas de 'Threads abertas' → uma linha em 'Decisões'
- Mantenha apenas threads relevantes; descarte resolvidas antigas
- Preserve Contracts verbatim; deixe Refs e Threads evaporarem se irrelevantes
- Comprima 'Decisões': uma linha por decisão, resumida
- 'Recuperáveis' guarda dNNN + dica de comando, nunca logs crus
- 'Última validação' guarda o último comando/teste/auditoria verificado
- 'Riscos' guarda riscos abertos

Retorne apenas o documento markdown atualizado. Sem markdown fence. Pronto."""
    return prompt


_TRUST_PREFIX_RE = re.compile(r'^\[(?:doctrine|state|inflight)\]\s*')
_TRAILING_TAG_RE = re.compile(r'\s*\[[^\]]*\]\s*$')
_SEQ_MARKER_RE = re.compile(r'\[seq \d+(?:-\d+)?\]')
_POINTER_CORE_MAX_CHARS = 80


def _compact_pointer_line(line: str) -> str:
    """Demote a Decisões/Refs entry to a compact Recuperáveis pointer.

    Keeps the seq_origem marker (grammar from c3ba90a) so the origin
    exchange stays recoverable from the journal. Refs entries keep only
    `path#Lx-y`; other entries keep a truncated core.
    """
    s = line.strip()
    seq_m = _SEQ_MARKER_RE.search(s)
    seq_marker = seq_m.group(0) if seq_m else ""

    ref_m = _REF_LINE_RE.match(s)
    if ref_m:
        l1, l2 = ref_m.group('l1'), ref_m.group('l2')
        loc = ""
        if l1:
            loc = f"#L{l1}" + (f"-{l2}" if l2 else "")
        core = f"{ref_m.group('path')}{loc}"
    else:
        core = _TRUST_PREFIX_RE.sub('', s)
        while True:
            m = _TRAILING_TAG_RE.search(core)
            if not m:
                break
            core = core[:m.start()]
        core = core.strip().splitlines()[0] if core.strip() else ""
        if len(core) > _POINTER_CORE_MAX_CHARS:
            core = core[:_POINTER_CORE_MAX_CHARS - 1].rstrip() + "…"

    return (core + (f" {seq_marker}" if seq_marker else "")).strip()


def enforce_budget_v3(
    md: str,
    budget_tokens: int = 2500,
    contract_ages: dict | None = None,
    turn: int = 0,
    max_age: int = 15,
    *,
    root=None,
    recoverables_max_items: int = 12,
    event_context: dict | None = None,
) -> str:
    """Fit a V3 doc into budget by DEMOTING before deleting (P6/A3).

    Order until within budget:
    1. Decisões (oldest first) demote to '## Recuperáveis' as compact
       pointer lines that keep their seq_origem marker;
    2. Refs (oldest first) demote the same way (path#Lx-y pointer);
    3. Riscos (oldest first) are deleted (legacy order preserved);
    4. only when Recuperáveis itself is full/over budget do real deletions
       happen there (oldest first).

    Every REAL deletion (cap eviction, Riscos trim, Recuperáveis trim) is
    reported via the owner_loop event `budget_evicted` when ``root`` is
    given — nothing disappears silently.

    Invariants (never violated even to meet budget):
    - Foco atual / Threads abertas / Contracts / Última validação are never
      trimmed here.
    """
    estimated_tokens = len(md) // 4
    if estimated_tokens <= budget_tokens:
        return md

    parsed = parse_living_v3(md)
    recoverables = parsed.get('Recuperáveis', [])
    cap = max(0, int(recoverables_max_items))
    evicted: list[str] = []

    # 1-2: demote Decisões then Refs into Recuperáveis pointers.
    for section in ('Decisões', 'Refs'):
        entries = parsed.get(section, [])
        while entries and (len(md) // 4) > budget_tokens:
            line = entries.pop(0)
            pointer = _compact_pointer_line(line)
            if pointer:
                while cap and len(recoverables) >= cap:
                    evicted.append(recoverables.pop(0))
                if cap:
                    recoverables.append(pointer)
                else:
                    evicted.append(line)
            parsed[section] = entries
            parsed['Recuperáveis'] = recoverables
            md = _rebuild_md_v3(parsed)
        if (len(md) // 4) <= budget_tokens:
            break

    # 3: Riscos are deleted oldest-first (no pointer form defined for them).
    entries = parsed.get('Riscos', [])
    while entries and (len(md) // 4) > budget_tokens:
        evicted.append(entries.pop(0))
        parsed['Riscos'] = entries
        md = _rebuild_md_v3(parsed)

    # 4: last resort — Recuperáveis itself is trimmed oldest-first.
    while recoverables and (len(md) // 4) > budget_tokens:
        evicted.append(recoverables.pop(0))
        parsed['Recuperáveis'] = recoverables
        md = _rebuild_md_v3(parsed)

    if evicted and root is not None:
        from . import owner_loop
        event = {
            "phase": "epochs",
            "event": "budget_evicted",
            "budget_tokens": budget_tokens,
            "evicted_count": len(evicted),
            "evicted_lines": evicted,
        }
        if event_context:
            event.update(event_context)
        owner_loop.log_owner_event(root, event)

    return md


def push_ring(root, chat_id: str, exchange: str) -> None:
    ring_path = ring_dir(root, chat_id)
    ring_path.mkdir(parents=True, exist_ok=True)

    slots = sorted([int(f.stem) for f in ring_path.glob('*.md') if f.stem.isdigit()])
    next_num = (slots[-1] if slots else -1) + 1

    slot_file = ring_path / f'{next_num:03d}.md'
    slot_file.write_text(exchange, encoding='utf-8')

    if len(slots) >= 10:
        oldest = ring_path / f'{slots[0]:03d}.md'
        if oldest.exists():
            oldest.unlink()


def _epochs_version(root) -> int:
    # Read the project config file DIRECTLY (no DEFAULT_CONFIG merge): a project
    # whose config.yaml carries an epochs.version honors it; a root with no
    # .burnless config falls back to 2 (V2 / backward-compatible capture path).
    # BURNLESS_EPOCH_V2 remains a temporary compatibility override for older
    # fixtures and manual testing; it does not need to be present in hooks.
    try:
        if os.environ.get("BURNLESS_EPOCH_V2"):
            return 3
        from . import paths
        cfg_path = paths.paths_for(str(Path(root) / ".burnless"))["config"]
        if not Path(cfg_path).exists():
            return 2
        import yaml
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return int(data.get("epochs", {}).get("version", 2))
    except Exception:
        return 2


def _compact_structure_gate_enabled(root: Path) -> bool:
    try:
        from . import config, paths
        cfg = config.load(paths.paths_for(Path(root) / ".burnless")["config"])
        return cfg.get("epochs", {}).get("compact_structure_gate", True)
    except Exception:
        return True


def apply_capture(root, chat_id: str, exchange: str, rewriter: Callable[[str], str | None] | None = None, *, version: int | None = None) -> Path:
    try:
        from . import recovery as recovery_mod
        root = Path(root)
        lp = living_path(root, chat_id)
        sp = state_path(root, chat_id)
        lock_path = sp.with_name(sp.name + ".lock")

        with recovery_mod._exclusive_lock(lock_path):
            prev_md = lp.read_text(encoding='utf-8') if lp.exists() else ""

            if is_noop(prev_md, exchange):
                lp.parent.mkdir(parents=True, exist_ok=True)
                if not lp.exists():
                    lp.write_text("", encoding='utf-8')
                push_ring(root, chat_id, exchange)
                return lp

            prev_state = {}
            if sp.exists():
                try:
                    prev_state = json.loads(sp.read_text(encoding='utf-8'))
                except Exception:
                    pass

            turn = prev_state.get('turn', 0) + 1
            prev_ages = prev_state.get('contract_ages', {})

            if rewriter is None:
                rewriter = living_rewriter(root)

            eff_version = version if version is not None else _epochs_version(root)

            if eff_version >= 3:
                prompt = living_rewrite_prompt_v3(prev_md, exchange)
            else:
                prompt = living_rewrite_prompt(prev_md, exchange)
            new_md = rewriter(prompt)

            if not new_md or not new_md.strip():
                lp.parent.mkdir(parents=True, exist_ok=True)
                if not lp.exists():
                    lp.write_text("", encoding='utf-8')
                push_ring(root, chat_id, exchange)
                return lp

            if eff_version >= 3 and _compact_structure_gate_enabled(root):
                parsed_gate = parse_living_v3(new_md)
                if not any(parsed_gate.get(s) for s in SECTIONS_V3):
                    try:
                        from . import recovery as recovery_mod2
                        recovery_mod2.record_hook_error(
                            root, hook="apply_capture_structure_reject", host="claude",
                            error=f"encoder output has zero v3 sections ({len(new_md)}B); previous doc kept",
                        )
                    except Exception:
                        pass
                    lp.parent.mkdir(parents=True, exist_ok=True)
                    if not lp.exists():
                        lp.write_text("", encoding='utf-8')
                    push_ring(root, chat_id, exchange)
                    return lp

            ages = update_contract_ages(prev_ages, new_md, turn)
            new_md = preserve_guard(prev_md, new_md, contract_ages=ages, turn=turn)
            new_md = preserve_open_threads(prev_md, new_md, exchange)
            if eff_version >= 3:
                new_md = enforce_budget_v3(
                    new_md,
                    contract_ages=ages,
                    turn=turn,
                    root=root,
                    event_context={"chat_id": chat_id},
                )
            else:
                new_md = enforce_budget(new_md, contract_ages=ages, turn=turn)
            ages = update_contract_ages(ages, new_md, turn)

            lp.parent.mkdir(parents=True, exist_ok=True)

            tmp = tempfile.NamedTemporaryFile(mode='w', dir=lp.parent, delete=False, encoding='utf-8')
            try:
                tmp.write(new_md)
                tmp.close()
                os.replace(tmp.name, lp)
            except Exception:
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass
                raise

            harvested = harvest_state(new_md)
            harvested['turn'] = turn
            harvested['contract_ages'] = ages
            recovery_mod._atomic_json_write(sp, harvested)

            push_ring(root, chat_id, exchange)

            return lp

    except Exception as exc:
        try:
            from . import recovery as recovery_mod
            recovery_mod.record_hook_error(root, hook="apply_capture", host="claude", error=f"{type(exc).__name__}: {exc}")
        except Exception:
            pass
        root = Path(root)
        lp = living_path(root, chat_id)
        lp.parent.mkdir(parents=True, exist_ok=True)
        if not lp.exists():
            lp.write_text("", encoding='utf-8')
        return lp


def living_rewriter(project_root) -> Callable[[str], str | None]:
    def _rewrite(prompt: str) -> str | None:
        try:
            from . import config, paths
            try:
                cfg = config.load(paths.paths_for(Path(project_root) / ".burnless")["config"])
            except Exception:
                cfg = {}
            enc = cfg.get("encoder") or {}
            provider = (enc.get("provider") or "anthropic").strip()
            model = enc.get("model") or config.DEFAULT_TIER_MODELS["bronze"]
        except Exception:
            return None

        if provider == "passthrough" or model == "passthrough":
            return None

        try:
            if provider == "ollama-local":
                # RM-2: endpoint/timeout come from config (encoder.endpoint,
                # encoder.timeout_s); BURNLESS_LOCAL_API is an override, not
                # the only path. Defaults: ollama :11434, 90s.
                local_api = (os.environ.get("BURNLESS_LOCAL_API") or str(enc.get("local_api") or "")).strip().lower()
                cfg_endpoint = str(enc.get("endpoint") or "").strip()
                try:
                    cfg_timeout = float(enc.get("timeout_s") or 0)
                except (TypeError, ValueError):
                    cfg_timeout = 0
                if local_api == "llamacpp":
                    url = cfg_endpoint or "http://localhost:11435/completion"
                    data = json.dumps({"prompt": prompt}).encode()
                    timeout_val = cfg_timeout or 120
                else:
                    url = cfg_endpoint or "http://localhost:11434/api/generate"
                    data = json.dumps(
                        {"model": model, "prompt": prompt, "system": ENCODER_SYSTEM_PROMPT, "stream": False}
                    ).encode()
                    timeout_val = cfg_timeout or 90

                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=timeout_val) as resp:
                    body = json.loads(resp.read())

                if local_api == "llamacpp":
                    out = body.get("content") or body.get("response") or ""
                else:
                    out = body.get("response", "")

                from .compression import _strip_gemma_channels
                out = _strip_gemma_channels(out)
            else:
                try:
                    from . import warm_session
                    from .warm_session import _claude_binary
                    claude_bin = _claude_binary() or "claude"
                except Exception:
                    claude_bin = "claude"
                try:
                    iso_cwd = warm_session.worker_cwd(Path(project_root) / ".burnless", model)
                except Exception:
                    iso_cwd = None
                result = subprocess.run(
                    [claude_bin, "-p", "--model", model, "--permission-mode", "bypassPermissions",
                     "--append-system-prompt", ENCODER_SYSTEM_PROMPT,
                     "--allowedTools", "", "--output-format", "json"],
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=iso_cwd,
                    env={**os.environ, "BURNLESS_NO_EPOCH": "1"},
                )
                data = json.loads(result.stdout)
                out = data["result"]

            out = out.strip()
            if out.startswith("```"):
                lines = out.split("\n")
                out = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

            return out if out else None
        except Exception:
            return None

    return _rewrite


def living_seed(root, chat_id: str) -> str:
    lp = living_path(root, chat_id)
    return lp.read_text(encoding='utf-8') if lp.exists() else ""
