"""Tests for preserve_open_threads guard (P10/4 — prevent thread evaporation)."""

import pytest
from burnless.epochs_v2 import preserve_open_threads, parse_living_v3


def test_evaporated_thread_is_reinjected():
    """Future-plan thread in prev but missing from new (evaporated) is reinjected into Threads abertas."""
    prev_md = """## Foco atual
- desenhar spec Import v2

## Threads abertas
- na volta construo Import v2 em fases (php -l + commit + deploy por fase) [inflight]

## Decisões

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""

    new_md = """## Foco atual
- spec Import v2 completo e deployado

## Threads abertas

## Decisões
- spec Import v2 entregue [chat:abc·t5]

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""

    result = preserve_open_threads(prev_md, new_md)
    assert "na volta construo Import v2" in result
    parsed = parse_living_v3(result)
    threads = parsed.get('Threads abertas', [])
    assert any("na volta construo Import v2" in t for t in threads), f"Thread not found in {threads}"


def test_thread_resolved_to_decisoes_not_reinjected():
    """If thread nucleus appears in Decisões (verbatim), it didn't evaporate — don't reinject."""
    prev_md = """## Foco atual
- fase 1 completa

## Threads abertas
- próximo passo implementar fase 2 com testes [inflight]

## Decisões

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""

    new_md = """## Foco atual
- fase 2 em progresso

## Threads abertas

## Decisões
- próximo passo implementar fase 2 com testes (decidido via silver tier) [chat:def·t6]

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""

    result = preserve_open_threads(prev_md, new_md)
    parsed = parse_living_v3(result)
    threads = parsed.get('Threads abertas', [])
    assert len(threads) == 0, f"Thread should not be reinjected if resolved verbatim in Decisões; got {threads}"


def test_generic_investigation_thread_is_preserved():
    """EVERY evaporated thread is reinjected — no keyword classification (guard is content-agnostic)."""
    prev_md = """## Foco atual
- auditoria de código

## Threads abertas
- investigar flakiness do teste de login no CI

## Decisões

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""

    new_md = """## Foco atual
- auditoria completa

## Threads abertas

## Decisões
- auditoria entregue

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""

    result = preserve_open_threads(prev_md, new_md)
    parsed = parse_living_v3(result)
    threads = parsed.get('Threads abertas', [])
    assert any("investigar flakiness do teste de login" in t for t in threads), (
        f"Generic thread must be preserved too — guard is content-agnostic; got {threads}"
    )


def test_thread_demoted_to_recuperaveis_not_reinjected():
    """If thread nucleus appears in Recuperáveis, it didn't evaporate — don't reinject."""
    prev_md = """## Foco atual
- auditoria de código

## Threads abertas
- revisar cobertura de testes (unidade + integração) [state]

## Decisões

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""

    new_md = """## Foco atual
- auditoria completa

## Threads abertas

## Decisões
- auditoria entregue

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
- revisar cobertura de testes (unidade + integração) [seq 12]
"""

    result = preserve_open_threads(prev_md, new_md)
    parsed = parse_living_v3(result)
    threads = parsed.get('Threads abertas', [])
    assert len(threads) == 0, f"Thread should not be reinjected if moved to Recuperáveis; got {threads}"


def test_missing_section_is_created():
    """If new_md lacks '## Threads abertas', it's created in canonical position (2nd section)."""
    prev_md = """## Foco atual
- implementar feature X

## Threads abertas
- testes de integração para feature X [inflight]

## Decisões

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""

    new_md = """## Foco atual
- feature X estrutura pronta

## Decisões
- estrutura criada

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""

    result = preserve_open_threads(prev_md, new_md)
    assert "## Threads abertas" in result
    parsed = parse_living_v3(result)
    threads = parsed.get('Threads abertas', [])
    assert any("testes de integração para feature X" in t for t in threads), f"Thread not found in {threads}"
    sections_order = [line[3:].strip() for line in result.split('\n') if line.startswith('## ')]
    assert sections_order[0] == "Foco atual", f"Foco atual should be first; got {sections_order}"
    assert sections_order[1] == "Threads abertas", f"Threads abertas should be 2nd; got {sections_order}"


def test_golden_design_done_build_pending():
    """Incident case: Foco='desenhar spec', thread='na volta construo' (future-plan), new_md='tudo completo' without thread."""
    prev_md = """## Foco atual
- desenhar spec Import v2

## Threads abertas
- [inflight] na volta construo Import v2 em fases (php -l + commit + deploy por fase)

## Decisões
- Import v2 arquitetura definida [chat:xyz·t3]

## Contracts

## Refs
- /Users/roberto/antigravity/burnless/src/burnless/epochs_v2.py#L465-L530 — spec location [seq 3]

## Riscos

## Última validação

## Recuperáveis
"""

    new_md = """## Foco atual
- tudo completo

## Threads abertas

## Decisões
- spec Import v2 commitada [chat:xyz·t7]
- spec Import v2 deployada [chat:xyz·t7]

## Contracts

## Refs
- /Users/roberto/antigravity/burnless/src/burnless/epochs_v2.py#L465-L530 — spec location [seq 3]

## Riscos

## Última validação
- pytest -q OK [seq 7]

## Recuperáveis
"""

    result = preserve_open_threads(prev_md, new_md)
    assert "na volta construo Import v2" in result
    parsed = parse_living_v3(result)
    threads = parsed.get('Threads abertas', [])
    assert len(threads) > 0, "Thread should be reinjected"
    assert any("na volta construo Import v2" in t for t in threads), f"Build thread not found in {threads}"


def test_golden_apply_capture_disk_roundtrip():
    """Incident case end-to-end: fake rewriter returns 'tudo completo' dropping the thread;
    after apply_capture the living doc ON DISK still contains the build thread."""
    import tempfile
    from pathlib import Path
    from burnless.epochs_v2 import apply_capture, living_path

    prev_md = """## Foco atual
- desenhar spec Import v2

## Threads abertas
- na volta construo Import v2 em fases (php -l + commit + deploy por fase) [inflight]

## Decisões

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""
    collapsed = """## Foco atual
- tudo completo

## Threads abertas

## Decisões
- spec Import v2 commitada e deployada [chat:abc·t7]

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""
    with tempfile.TemporaryDirectory() as tmp_root:
        tmp_root = Path(tmp_root)
        chat_id = "chat_golden"
        lp = living_path(tmp_root, chat_id)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(prev_md, encoding="utf-8")

        exchange = "user: e ai, commitou?\nassistant: spec Import v2 commitada e deployada" + " detalhe" * 40

        def collapsing_rewriter(prompt: str) -> str:
            return collapsed

        apply_capture(tmp_root, chat_id, exchange, rewriter=collapsing_rewriter, version=3)

        disk = lp.read_text(encoding="utf-8")
        assert "na volta construo Import v2" in disk, "thread evaporated on disk despite guard"


def test_thread_closed_by_exchange_may_evaporate():
    """A thread the current exchange explicitly discusses may be dropped by the rewriter."""
    prev_md = """## Foco atual
- estabilizar CI

## Threads abertas
- investigar flakiness do teste de login no CI [inflight]

## Decisões

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""
    new_md = """## Foco atual
- estabilizar CI

## Threads abertas

## Decisões

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""
    exchange = (
        "user: sobre o flakiness do teste de login: achei a causa no fixture, pode encerrar\n"
        "assistant: thread encerrada, causa era o relógio do fixture"
    )
    result = preserve_open_threads(prev_md, new_md, exchange)
    parsed = parse_living_v3(result)
    threads = parsed.get('Threads abertas', [])
    assert len(threads) == 0, f"Exchange discussed the thread — closure must be allowed; got {threads}"


def test_thread_untouched_by_exchange_cannot_evaporate():
    """A thread the exchange never mentioned cannot be dropped, whatever the rewriter says."""
    prev_md = """## Foco atual
- desenhar spec Import v2

## Threads abertas
- na volta construo Import v2 em fases (php -l + commit + deploy por fase) [inflight]

## Decisões

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""
    new_md = """## Foco atual
- tudo completo

## Threads abertas

## Decisões
- spec entregue

## Contracts

## Refs

## Riscos

## Última validação

## Recuperáveis
"""
    exchange = "user: e ai, commitou?\nassistant: spec Import v2 commitada e deployada"
    result = preserve_open_threads(prev_md, new_md, exchange)
    assert "na volta construo Import v2" in result
