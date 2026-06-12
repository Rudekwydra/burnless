from __future__ import annotations

_EXPAND_PROMPT = (
    "Expanda a resposta abaixo em portugues claro e natural para exibicao ao usuario, "
    "sem adicionar fatos novos, max 2x o tamanho original:\n\n"
)


def expand_for_display(text: str, ollama_fn) -> str:
    if ollama_fn is None:
        return text
    try:
        result = ollama_fn(_EXPAND_PROMPT + text)
        if result and result.strip():
            return result.strip()
        return text
    except Exception:
        return text
