"""Tests for the deterministic compression layer (telegrafista regex).

The LLM stage is not unit-tested here — that requires Ollama running and is
covered by examples/plugins/burnless-compress/integration_test.py. These tests
focus on the deterministic Stage 2 which must be:

  - Idempotent: f(f(x)) == f(x)
  - Stopword-removing: drops 'o', 'a', 'que', 'the', 'for', 'with'...
  - Intent-preserving: keeps file paths, library names, numbers, action verbs
  - Token-improving (with tiktoken): never increases total tokens on a corpus
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add bench/ to path so we can import filter_entrada_spike (not a package)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))

from filter_entrada_spike import (  # noqa: E402
    TELEGRAFISTA_STOPWORDS,
    deterministic_squeeze,
)


def test_drops_articles_and_preps_pt():
    out = deterministic_squeeze("implementa o teste de cache no claude -p")
    # Filler words gone
    for w in ["o", "de", "no"]:
        assert f" {w} " not in f" {out} ".lower(), f"Expected {w!r} dropped from {out!r}"
    # Action keywords preserved
    assert "implementa" in out
    assert "teste" in out
    assert "cache" in out
    assert "claude" in out


def test_drops_articles_and_preps_en():
    out = deterministic_squeeze("fix the bug in the auth.py login flow with verbose logs")
    for w in ["the", "in", "with"]:
        assert f" {w} " not in f" {out} ".lower(), f"Expected {w!r} dropped from {out!r}"
    assert "fix" in out
    assert "bug" in out
    assert "auth.py" in out


def test_preserves_file_paths_and_libraries():
    out = deterministic_squeeze(
        "escreve um script python com pandas que leia data.csv e use numpy"
    )
    assert "data.csv" in out
    assert "pandas" in out
    assert "numpy" in out
    assert "python" in out
    assert "script" in out


def test_preserves_numbers_and_versions():
    out = deterministic_squeeze(
        "a query tá demorando 8 segundos pra carregar agora, antes era 2 segundos"
    )
    assert "8" in out
    assert "2" in out


def test_idempotent():
    """f(f(x)) == f(x). Running squeeze twice produces the same output as once."""
    samples = [
        "implementa o teste de cache no claude -p",
        "fix the bug in auth.py login flow",
        "preciso urgente que voce escreva um script python",
        "",
        "single",
        "claude -p --output-format json",
    ]
    for sample in samples:
        once = deterministic_squeeze(sample)
        twice = deterministic_squeeze(once)
        assert once == twice, f"Not idempotent: {sample!r} -> {once!r} -> {twice!r}"


def test_empty_input():
    assert deterministic_squeeze("") == ""


def test_only_stopwords_collapses_to_empty():
    """A string of pure stopwords should collapse to empty."""
    out = deterministic_squeeze("o a de que para com the for with")
    assert out == ""


def test_stopword_set_includes_high_frequency_words():
    """Spot-check the dict contains common PT/EN single-token fillers."""
    expected_in_set = ["o", "a", "que", "para", "com", "the", "for", "with"]
    for w in expected_in_set:
        assert w in TELEGRAFISTA_STOPWORDS, f"{w!r} should be in stopwords"


def test_punctuation_runs_collapse():
    """Multiple consecutive '.' or ',' collapse to a single one."""
    out = deterministic_squeeze("ai..., meu deus,, refatora isso")
    assert "..." not in out
    assert ",," not in out


def test_token_count_no_worse_with_tiktoken():
    """Telegrafista must never INCREASE total tokens on natural-language corpus.

    If a stopword in the dict tokenizes as MORE tokens than what it replaces,
    this test catches the regression. cf. May 2026 finding that 'thx', 'w/',
    'pls' tokenize as more BPE tokens than 'thank you', 'with', 'please'.
    """
    try:
        import tiktoken
    except ImportError:
        return  # skip silently — tiktoken is optional
    enc = tiktoken.get_encoding("cl100k_base")
    corpus = [
        "preciso urgente que voce escreva um script python que leia um csv chamado dados.csv com colunas nome idade salario",
        "olha por favor implementa o teste de cache no claude -p mas com haiku primeiro pra economizar quota",
        "fiquei triste agora mas pensa direito acho que pode dar certo se a gente conseguir filtrar antes do LLM caro",
        "fix the bug in the upload function for files larger than 50MB before reaching the app",
    ]
    total_orig = sum(len(enc.encode(s)) for s in corpus)
    total_sqz = sum(len(enc.encode(deterministic_squeeze(s))) for s in corpus)
    # Hard guarantee: never worse on natural language
    assert total_sqz <= total_orig, (
        f"Telegrafista increased tokens: {total_orig} -> {total_sqz}. "
        "Some stopword in the dict tokenizes as MORE tokens than removing it saves."
    )
    # Soft expectation: at least 5% reduction on this corpus
    assert total_sqz < total_orig * 0.95, (
        f"Telegrafista compressed less than 5% on natural PT/EN corpus: {total_orig} -> {total_sqz}"
    )
