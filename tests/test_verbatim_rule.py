import pytest
from burnless.epochs_v2 import living_rewrite_prompt_v3


def test_prompt_has_verbatim_rule():
    """Verificar que living_rewrite_prompt_v3 contém a regra VERBATIM crítica."""
    prompt = living_rewrite_prompt_v3("", "x")

    assert "VERBATIM" in prompt, "Marcador 'VERBATIM' não encontrado no prompt"
    assert "TRANSDUTOR" in prompt, "Marcador 'TRANSDUTOR' não encontrado no prompt"
    assert "substring exata" in prompt, "Frase 'substring exata' não encontrada no prompt"


def test_six_axes_still_present():
    """Verificar que os 6 eixos de consolidação continuam presentes após adição da regra VERBATIM."""
    prompt = living_rewrite_prompt_v3("", "x")

    axes = ['Provenance', 'Supersede', 'Trust-boundary', 'Deletion', 'Slot-routing', 'Evidence-retrieval']
    for axis in axes:
        assert axis in prompt, f"Eixo '{axis}' não encontrado no prompt após mudança VERBATIM"
