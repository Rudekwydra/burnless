import json
from burnless.contextgc import load_transcript, collapse, expand, measure

TRANSCRIPT = "/Users/roberto/.claude/projects/-Users-roberto-antigravity/3759ce88-2453-4433-bbc5-2904ad7e3f9d.jsonl"


def test_fidelity():
    """Verifica fidelidade 100% na re-expansão de ponteiros."""
    collapsed_events, index = collapse(TRANSCRIPT)

    # Extrai todos os ponteiros
    pointers = []
    for event in collapsed_events:
        if isinstance(event["content"], list):
            for block in event["content"]:
                if isinstance(block, dict) and "ptr" in block:
                    pointers.append(block)

    # Para cada ponteiro, expande e compara com o original
    original_events = load_transcript(TRANSCRIPT)
    for pointer in pointers:
        ref_id = pointer["ptr"]
        expanded = expand(ref_id, index)

        # Reconstrói o bloco original
        original_event = original_events[pointer["line"]]
        original_block = original_event["content"][pointer["block"]]

        # Compara (deve ser idêntico)
        assert expanded == original_block, f"Fidelity mismatch para {ref_id}"

    print(f"✓ Fidelidade verificada: {len(pointers)} ponteiros re-expandidos corretamente")


def test_reduction():
    """Verifica que reduction >= 40%."""
    result = measure(TRANSCRIPT)
    reduction = result["reduction_pct"]
    assert reduction >= 40.0, f"Redução {reduction}% < 40%"
    print(f"✓ Redução verificada: {reduction}%")


def test_hash_drift():
    """Ponteiro carrega hash; expand detecta drift do source."""
    collapsed_events, index = collapse(TRANSCRIPT)
    pointers = [b for e in collapsed_events if isinstance(e["content"], list)
                for b in e["content"] if isinstance(b, dict) and "ptr" in b]
    assert pointers, "nenhum ponteiro gerado"
    for p in pointers:
        assert len(p.get("hash", "")) == 16, f"ponteiro {p['ptr']} sem hash"
    rid = pointers[0]["ptr"]
    expand(rid, index)  # re-fetch limpo não levanta
    index[rid]["hash"] = "deadbeefdeadbeef"
    try:
        expand(rid, index)
        raise AssertionError("drift não detectado")
    except ValueError as e:
        assert "mismatch" in str(e)
    print(f"✓ Hash drift verificado: {len(pointers)} ponteiros com hash")


def main():
    """Executa testes e imprime resultado final."""
    test_fidelity()
    test_reduction()
    test_hash_drift()
    result = measure(TRANSCRIPT)
    print(f"GC_REDUCTION={result['reduction_pct']}")


if __name__ == "__main__":
    main()
