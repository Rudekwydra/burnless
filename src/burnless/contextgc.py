import json
import hashlib
from typing import Any, Dict, List, Tuple

def _tok(s: str) -> int:
    """Estimador de tokens: (len(s) + 3) // 4"""
    return (len(s) + 3) // 4


def _hash(block: Any) -> str:
    """Hash determinístico do bloco (sha256 truncado) para verificar re-fetch."""
    canon = json.dumps(block, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def load_transcript(path: str) -> List[Dict[str, Any]]:
    """
    Lê .jsonl linha a linha. Para cada linha JSON válida, extrai:
    - role: obj["message"]["role"] (default "user")
    - content: obj["message"]["content"] (pode ser str ou lista)

    Retorna lista de eventos {"line": line_no, "role": role, "content": content}.
    Linhas inválidas (JSON quebrado) puladas silenciosamente.
    """
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                message = obj.get("message", {})
                role = message.get("role", "user")
                content = message.get("content", [])
                events.append({
                    "line": line_no,
                    "role": role,
                    "content": content
                })
            except (json.JSONDecodeError, KeyError, TypeError):
                # Pula linhas inválidas
                continue
    return events


def collapse(path: str, keep_last_turns: int = 2) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Collapsa blocos de tool_use/tool_result antigos em ponteiros.

    1. Carrega eventos via load_transcript.
    2. Conta turnos: incrementa turn quando role=="user" e content é str.
    3. Descobre max_turn.
    4. Para cada bloco em content (quando lista), se type in ("tool_use", "tool_result")
       e turno do evento < (max_turn - keep_last_turns), substitui por ponteiro.

    Ponteiro: {"ptr": ref_id, "kind": type, "tool": tool_name_or_None, "tok": tok_original, "src": path, "line": line_no, "block": block_index}
    ref_id = f"gc:{line_no}:{block_index}"

    Blocos de texto e todo I/O dos keep_last_turns turnos mais recentes ficam intactos.

    Retorna: (collapsed_events, index)
    index: dict ref_id -> {"src": path, "line": line_no, "block": block_index}
    """
    events = load_transcript(path)

    # Primeiro passo: contar turnos e achar max_turn
    turn = 0
    max_turn = 0
    for event in events:
        if event["role"] == "user" and isinstance(event["content"], str):
            max_turn = turn
            turn += 1

    # Segundo passo: colapsar blocos antigos
    collapsed_events = []
    index = {}

    for event in events:
        collapsed_event = event.copy()

        if isinstance(event["content"], list):
            collapsed_content = []
            turn_counter = 0

            # Re-calcula turn para este evento específico (contando up-to event)
            for e in events:
                if e["line"] == event["line"]:
                    break
                if e["role"] == "user" and isinstance(e["content"], str):
                    turn_counter += 1

            event_turn = turn_counter

            for block_index, block in enumerate(event["content"]):
                if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                    if event_turn < (max_turn - keep_last_turns):
                        # Colapsa este bloco
                        ref_id = f"gc:{event['line']}:{block_index}"
                        tool_name = block.get("name") if block.get("type") == "tool_use" else None
                        tok_original = _tok(json.dumps(block, ensure_ascii=False))
                        blk_hash = _hash(block)

                        pointer = {
                            "ptr": ref_id,
                            "kind": block.get("type"),
                            "tool": tool_name,
                            "tok": tok_original,
                            "src": path,
                            "line": event["line"],
                            "block": block_index,
                            "hash": blk_hash
                        }

                        collapsed_content.append(pointer)
                        index[ref_id] = {
                            "src": path,
                            "line": event["line"],
                            "block": block_index,
                            "hash": blk_hash
                        }
                    else:
                        # Mantém bloco intacto (within keep_last_turns)
                        collapsed_content.append(block)
                else:
                    # Mantém blocos de texto
                    collapsed_content.append(block)

            collapsed_event["content"] = collapsed_content

        collapsed_events.append(collapsed_event)

    return collapsed_events, index


def expand(ref_id: str, index: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Re-expande um ponteiro usando o index.

    Usa index[ref_id] para abrir o .jsonl em src, ler a linha line,
    e retornar o conteúdo raw original do bloco em posição block.
    Byte-a-byte idêntico ao original.
    """
    if ref_id not in index:
        raise ValueError(f"ref_id {ref_id} não encontrado no index")

    ref_info = index[ref_id]
    src = ref_info["src"]
    line_no = ref_info["line"]
    block_index = ref_info["block"]

    with open(src, "r", encoding="utf-8") as f:
        for current_line_no, line in enumerate(f):
            if current_line_no == line_no:
                obj = json.loads(line.rstrip("\n"))
                message = obj.get("message", {})
                content = message.get("content", [])

                if isinstance(content, list) and block_index < len(content):
                    block = content[block_index]
                    expected = ref_info.get("hash")
                    if expected is not None and _hash(block) != expected:
                        raise ValueError(
                            f"Hash mismatch para ref {ref_id}: source drifted"
                        )
                    return block
                else:
                    raise ValueError(f"Block {block_index} não encontrado na linha {line_no}")

    raise ValueError(f"Linha {line_no} não encontrada em {src}")


def measure(path: str, keep_last_turns: int = 2) -> Dict[str, Any]:
    """
    Mede redução de espaço após collapse.

    Retorna {"total_tok": ..., "collapsed_tok": ..., "reduction_pct": ...}
    """
    events = load_transcript(path)

    # Total tokens before collapse
    total_tok = 0
    for event in events:
        if isinstance(event["content"], list):
            for block in event["content"]:
                if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                    total_tok += _tok(json.dumps(block, ensure_ascii=False))

    # Collapsed tokens
    collapsed_events, index = collapse(path, keep_last_turns)
    collapsed_tok = 0
    for event in collapsed_events:
        if isinstance(event["content"], list):
            for block in event["content"]:
                if isinstance(block, dict):
                    if "ptr" in block:
                        # É um ponteiro
                        collapsed_tok += _tok(json.dumps(block, ensure_ascii=False))
                    elif block.get("type") in ("tool_use", "tool_result"):
                        # Bloco original (não colapsado)
                        collapsed_tok += _tok(json.dumps(block, ensure_ascii=False))

    reduction_pct = round((1 - collapsed_tok / total_tok) * 100, 1) if total_tok > 0 else 0.0

    return {
        "total_tok": total_tok,
        "collapsed_tok": collapsed_tok,
        "reduction_pct": reduction_pct
    }
