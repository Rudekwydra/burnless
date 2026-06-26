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


def harvest_state(md: str) -> dict:
    parsed = parse_living(md)
    contracts = [line.lstrip('- ').strip() for line in parsed.get('Contracts', [])]
    refs = [line.lstrip('- ').strip() for line in parsed.get('Refs', [])]
    open_threads = [line.lstrip('- ').strip() for line in parsed.get('Threads abertas', [])]
    return {
        "contracts": contracts,
        "refs": refs,
        "open_threads": open_threads,
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

## Documento anterior (vazio se primeira vez)
```
{prev_md if prev_md else '<vazio>'}
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
    """
    result = {section: [] for section in SECTIONS_V3}
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


def _rebuild_md_v3(parsed: dict[str, list[str]]) -> str:
    lines = []
    for section in SECTIONS_V3:
        lines.append(f'## {section}')
        for body_line in parsed.get(section, []):
            if not body_line.startswith('- '):
                lines.append(f'- {body_line}')
            else:
                lines.append(body_line)
        lines.append('')
    return '\n'.join(lines)


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

## Documento anterior (vazio se primeira vez)
```
{prev_md if prev_md else '<vazio>'}
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


def enforce_budget_v3(md: str, budget_tokens: int = 2500, contract_ages: dict | None = None, turn: int = 0, max_age: int = 15) -> str:
    """Trim a V3 doc to fit budget while honoring V3 invariants.

    Trim order until within budget: Decisões (oldest first), then Refs
    (oldest first), then Riscos (oldest first).

    Invariants (never violated even to meet budget):
    - Foco atual is never trimmed here (never reduced to empty while
      Threads abertas is non-empty).
    - A Contracts line whose first extracted entity appears anywhere in
      Threads abertas text is pinned (never removed).
    """
    estimated_tokens = len(md) // 4
    if estimated_tokens <= budget_tokens:
        return md

    parsed = parse_living_v3(md)

    for section in ('Decisões', 'Refs', 'Riscos'):
        entries = parsed.get(section, [])
        while entries and (len(md) // 4) > budget_tokens:
            entries.pop(0)
            parsed[section] = entries
            md = _rebuild_md_v3(parsed)
        if (len(md) // 4) <= budget_tokens:
            break

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


def apply_capture(root, chat_id: str, exchange: str, rewriter: Callable[[str], str | None] | None = None) -> Path:
    try:
        root = Path(root)
        lp = living_path(root, chat_id)
        sp = state_path(root, chat_id)

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

        prompt = living_rewrite_prompt(prev_md, exchange)
        new_md = rewriter(prompt)

        if not new_md or not new_md.strip():
            lp.parent.mkdir(parents=True, exist_ok=True)
            if not lp.exists():
                lp.write_text("", encoding='utf-8')
            push_ring(root, chat_id, exchange)
            return lp

        ages = update_contract_ages(prev_ages, new_md, turn)
        new_md = preserve_guard(prev_md, new_md, contract_ages=ages, turn=turn)
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
        sp.write_text(json.dumps(harvested, ensure_ascii=False, indent=2), encoding='utf-8')

        push_ring(root, chat_id, exchange)

        return lp

    except Exception:
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
                data = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
                req = urllib.request.Request(
                    "http://localhost:11434/api/generate",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    body = json.loads(resp.read())
                out = body["response"]
                from .compression import _strip_gemma_channels
                out = _strip_gemma_channels(out)
            else:
                try:
                    from .warm_session import _claude_binary
                    claude_bin = _claude_binary() or "claude"
                except Exception:
                    claude_bin = "claude"
                result = subprocess.run(
                    [claude_bin, "-p", "--model", model, "--permission-mode", "bypassPermissions",
                     "--allowedTools", "", "--output-format", "json"],
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=60,
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
