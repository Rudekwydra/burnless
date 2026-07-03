"""Tests for Living Memory V3 (8-section, additive) in epochs_v2.py."""
from __future__ import annotations

import burnless.epochs_v2 as e


def _v3_doc() -> str:
    return (
        "## Foco atual\n- imp V3 sections\n\n"
        "## Threads abertas\n- wire V3 into config\n\n"
        "## Decisões\n- adotar 8-section model\n\n"
        "## Contracts\n- d000 keep V2 green\n\n"
        "## Refs\n- /Users/roberto/antigravity/burnless/docs/DOCTRINE.md\n\n"
        "## Riscos\n- regressão nos 37 testes V2\n\n"
        "## Última validação\n- pytest -q OK\n\n"
        "## Recuperáveis\n- d725 — pytest tests/test_epochs_v3.py\n"
    )


def _v2_doc() -> str:
    return (
        "## Foco atual\n- foco x\n\n"
        "## Threads abertas\n- thread y\n\n"
        "## Decisões\n- decisão z\n\n"
        "## Contracts\n- d000 contrato\n\n"
        "## Refs\n- /path/ref\n"
    )


def test_parse_v3_eight_section_doc():
    parsed = e.parse_living_v3(_v3_doc())
    assert set(e.SECTIONS_V3).issubset(parsed.keys())
    assert len([k for k in parsed if k in e.SECTIONS_V3]) == 8
    assert parsed["Foco atual"] == ["imp V3 sections"]
    assert parsed["Riscos"] == ["regressão nos 37 testes V2"]
    assert parsed["Última validação"] == ["pytest -q OK"]
    assert parsed["Recuperáveis"] == ["d725 — pytest tests/test_epochs_v3.py"]


def test_parse_v3_accepts_v2_doc():
    parsed = e.parse_living_v3(_v2_doc())
    assert set(e.SECTIONS_V3).issubset(parsed.keys())
    assert parsed["Foco atual"] == ["foco x"]
    assert parsed["Contracts"] == ["d000 contrato"]
    assert parsed["Riscos"] == []
    assert parsed["Última validação"] == []
    assert parsed["Recuperáveis"] == []


def test_parse_v3_ignores_instruction_header_and_vazio():
    md = (
        "## Documento completo atualizado\n"
        "- <vazio>\n"
        "## Foco atual\n"
        "- real focus\n"
    )
    parsed = e.parse_living_v3(md)
    assert parsed["Foco atual"] == ["real focus"]
    assert "Documento completo atualizado" not in parsed


def test_rebuild_v3_roundtrip_and_header_order():
    parsed = e.parse_living_v3(_v3_doc())
    md = e._rebuild_md_v3(parsed)
    reparsed = e.parse_living_v3(md)
    for section in e.SECTIONS_V3:
        assert reparsed[section] == parsed[section]

    # headers appear in canonical order
    positions = [md.find(f"## {s}") for s in e.SECTIONS_V3]
    assert all(p != -1 for p in positions)
    assert positions == sorted(positions)


def test_rewrite_prompt_v3_mentions_all_sections():
    prompt = e.living_rewrite_prompt_v3("", "alguma troca")
    for section in e.SECTIONS_V3:
        assert section in prompt
    assert "Recuperáveis" in prompt
    assert "Última validação" in prompt


def test_enforce_budget_v3_trims_decisoes_oldest_first():
    foco = "## Foco atual\n- mantém foco\n\n"
    threads = "## Threads abertas\n\n"
    decisoes = "## Decisões\n" + "".join(
        f"- decisão {i} " + ("x" * 600) + "\n" for i in range(20)
    ) + "\n"
    rest = "## Contracts\n\n## Refs\n\n## Riscos\n\n## Última validação\n\n## Recuperáveis\n"
    md = foco + threads + decisoes + rest

    assert len(md) // 4 > 2500
    out = e.enforce_budget_v3(md, budget_tokens=2500)
    assert len(out) // 4 <= 2500

    parsed = e.parse_living_v3(out)
    # Foco atual stays non-empty
    assert parsed["Foco atual"] == ["mantém foco"]
    # oldest decisões dropped first → surviving ones are the higher indices
    survivors = parsed["Decisões"]
    assert survivors  # not all dropped here
    assert "decisão 0 " not in "".join(survivors)
    assert any("decisão 19 " in s for s in survivors)


def test_enforce_budget_v3_pins_contract_referenced_in_threads():
    foco = "## Foco atual\n- foco\n\n"
    threads = "## Threads abertas\n- ainda mexendo em d000\n\n"
    decisoes = "## Decisões\n" + "".join(
        f"- decisão {i} " + ("y" * 600) + "\n" for i in range(20)
    ) + "\n"
    contracts = "## Contracts\n- d000 contrato ativo verbatim\n\n"
    rest = "## Refs\n\n## Riscos\n\n## Última validação\n\n## Recuperáveis\n"
    md = foco + threads + decisoes + contracts + rest

    assert len(md) // 4 > 2500
    out = e.enforce_budget_v3(md, budget_tokens=2500)

    parsed = e.parse_living_v3(out)
    # contract referenced by open thread survives trimming
    assert any("d000 contrato ativo verbatim" in c for c in parsed["Contracts"])


def test_enforce_budget_v3_trim_order_decisoes_then_refs_then_riscos():
    # Sizes chosen so that dropping ALL Decisões is not enough; Refs must be hit.
    # Riscos must remain untouched until Refs is exhausted.
    foco = "## Foco atual\n- foco\n\n"
    threads = "## Threads abertas\n\n"
    # Decisões alone (4k) is small; Refs alone (>12k) exceeds budget so even
    # after exhausting Decisões, Refs must be trimmed — but only partially.
    decisoes = "## Decisões\n" + "".join(
        f"- d{i} " + ("a" * 400) + "\n" for i in range(10)
    ) + "\n"
    refs = "## Refs\n" + "".join(
        f"- r{i} " + ("b" * 400) + "\n" for i in range(30)
    ) + "\n"
    riscos = "## Riscos\n" + "".join(
        f"- risk{i} keep me\n" for i in range(5)
    ) + "\n"
    contracts = "## Contracts\n\n"
    tail = "## Última validação\n\n## Recuperáveis\n"
    md = foco + threads + decisoes + contracts + refs + riscos + tail

    assert len(md) // 4 > 2500
    out = e.enforce_budget_v3(md, budget_tokens=2500)
    assert len(out) // 4 <= 2500

    parsed = e.parse_living_v3(out)
    # Decisões fully exhausted before Refs touched → all decisões gone
    assert parsed["Decisões"] == []
    # Refs partially trimmed but Riscos untouched (Refs not yet exhausted)
    assert 0 < len(parsed["Refs"]) < 30
    assert len(parsed["Riscos"]) == 5
