import pytest
from burnless.epochs_v2 import living_rewrite_prompt_v3


def test_prompt_has_six_axes():
    """Verificar que living_rewrite_prompt_v3 contém os 6 eixos de consolidação."""
    prompt = living_rewrite_prompt_v3("", "x")

    axes = ['Provenance', 'Supersede', 'Trust-boundary', 'Deletion', 'Slot-routing', 'Evidence-retrieval']
    for axis in axes:
        assert axis in prompt, f"Eixo '{axis}' não encontrado no prompt"

    assert '[doctrine]' in prompt, "Tag '[doctrine]' não encontrada"
    assert '[inflight]' in prompt, "Tag '[inflight]' não encontrada"


def test_prompt_signature_unchanged():
    """Verificar que living_rewrite_prompt_v3 mantém assinatura esperada."""
    result = living_rewrite_prompt_v3("", "x")
    assert isinstance(result, str), "living_rewrite_prompt_v3 deve retornar string"

    result_with_budget = living_rewrite_prompt_v3("", "x", 2500)
    assert isinstance(result_with_budget, str), "living_rewrite_prompt_v3 com 3 args deve retornar string"
    assert result == result_with_budget, "Resultado com 2 e 3 args deve ser idêntico quando budget=2500"
