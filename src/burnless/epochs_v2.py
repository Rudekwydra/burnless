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

# Entity patterns: absolute paths, delegation ids, commit hashes, file.ext tokens
_ENTITY_PATTERNS = [
    re.compile(r'/[\w][\w./\-]+'),
    re.compile(r'\bd\d{2,4}\b'),
    re.compile(r'\b[0-9a-f]{7,40}\b'),
    re.compile(r'\b[\w\-]+\.[A-Za-z]{1,5}\b'),
]


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


def is_noop(prev_md: str, exchange: str, max_len: int = 240) -> bool:
    exchange_stripped = exchange.strip()
    if len(exchange_stripped) <= max_len:
        exchange_entities = extract_entities(exchange_stripped)
        prev_entities = extract_entities(prev_md)
        return exchange_entities.issubset(prev_entities)
    return False


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


def preserve_guard(prev_md: str, new_md: str) -> str:
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


def enforce_budget(md: str, budget_tokens: int = 2500) -> str:
    estimated_tokens = len(md) // 4
    if estimated_tokens <= budget_tokens:
        return md

    parsed = parse_living(md)
    decisoes = parsed.get('Decisões', [])

    while decisoes and (len(md) // 4) > budget_tokens:
        decisoes.pop(0)
        parsed['Decisões'] = decisoes
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

        new_md = preserve_guard(prev_md, new_md)
        new_md = enforce_budget(new_md)

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
