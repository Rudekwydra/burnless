"""Tests for owner_loop.refine_seed()."""

import json
import tempfile
from pathlib import Path

import pytest

from burnless.owner_loop import refine_seed
from burnless.owner_cache import read_valid_refined_seed, compute_base_fingerprint


# Fixture: minimal valid floor
@pytest.fixture
def floor_md():
    return """## Foco atual
- trabalho em progresso

## Threads abertas
- thread-1

## Decisões
- decision-1

## Contracts
- /path/to/contract

## Refs
- https://example.com
"""


# Fixture: minimal predecessors (newest-first)
@pytest.fixture
def predecessors():
    return [
        ("chat-1", "doc content 1"),
        ("chat-0", "doc content 0"),
    ]


# Fixture: temp cache directory
@pytest.fixture
def cache_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def test_valid_refinement_written(floor_md, predecessors, cache_dir):
    """Rewriter returns reorganized floor (valid); cache written, True returned."""
    cache_path = Path(cache_dir) / "cache.json"

    # Rewriter: reorganize and tag a decision with [inflight]
    def fake_rewriter(prompt):
        return floor_md.replace("- decision-1", "- [inflight] decision-1")

    result = refine_seed(
        cache_path=str(cache_path),
        predecessors=predecessors,
        floor_md=floor_md,
        rewriter=fake_rewriter,
        owner_model="gemma",
        generated_at="2026-06-30T00:00:00Z",
        exchange="",
    )

    assert result is True
    assert cache_path.exists()

    # Verify cache content matches (must pass same owner_model and prompt_version as refine_seed)
    fp = compute_base_fingerprint(predecessors, owner_model="gemma", prompt_version="v3")
    cached_md = read_valid_refined_seed(str(cache_path), fp)
    assert cached_md is not None
    assert "[inflight] decision-1" in cached_md


def test_hallucination_not_written(floor_md, predecessors, cache_dir):
    """Rewriter adds unsupported line; validate bans it → result == floor → False, no cache."""
    cache_path = Path(cache_dir) / "cache.json"

    # Rewriter: add invented line NOT in floor
    def fake_rewriter(prompt):
        return floor_md + "\n- completely invented hallucination"

    result = refine_seed(
        cache_path=str(cache_path),
        predecessors=predecessors,
        floor_md=floor_md,
        rewriter=fake_rewriter,
        owner_model="gemma",
        generated_at="2026-06-30T00:00:00Z",
        exchange="",
    )

    # validate_owner_output bans hallucination, returns floor → result == floor → False
    assert result is False
    assert not cache_path.exists()


def test_rewriter_exception_failclosed(floor_md, predecessors, cache_dir):
    """Rewriter raises exception; refine_seed returns False, no cache written."""
    cache_path = Path(cache_dir) / "cache.json"

    def fake_rewriter(prompt):
        raise ValueError("rewriter crashed")

    result = refine_seed(
        cache_path=str(cache_path),
        predecessors=predecessors,
        floor_md=floor_md,
        rewriter=fake_rewriter,
        owner_model="gemma",
        generated_at="2026-06-30T00:00:00Z",
        exchange="",
    )

    assert result is False
    assert not cache_path.exists()


def test_rewriter_empty_failclosed(floor_md, predecessors, cache_dir):
    """Rewriter returns empty string or None; returns False, no cache."""
    cache_path = Path(cache_dir) / "cache.json"

    # Test empty string
    def fake_rewriter_empty(prompt):
        return ""

    result = refine_seed(
        cache_path=str(cache_path),
        predecessors=predecessors,
        floor_md=floor_md,
        rewriter=fake_rewriter_empty,
        owner_model="gemma",
        generated_at="2026-06-30T00:00:00Z",
        exchange="",
    )
    assert result is False
    assert not cache_path.exists()

    # Test None
    def fake_rewriter_none(prompt):
        return None

    result = refine_seed(
        cache_path=str(cache_path),
        predecessors=predecessors,
        floor_md=floor_md,
        rewriter=fake_rewriter_none,
        owner_model="gemma",
        generated_at="2026-06-30T00:00:00Z",
        exchange="",
    )
    assert result is False
    assert not cache_path.exists()


def test_identical_to_floor_not_written(floor_md, predecessors, cache_dir):
    """Rewriter returns exactly the floor; returns False (no redundant cache)."""
    cache_path = Path(cache_dir) / "cache.json"

    def fake_rewriter_identical(prompt):
        return floor_md

    result = refine_seed(
        cache_path=str(cache_path),
        predecessors=predecessors,
        floor_md=floor_md,
        rewriter=fake_rewriter_identical,
        owner_model="gemma",
        generated_at="2026-06-30T00:00:00Z",
        exchange="",
    )

    assert result is False
    assert not cache_path.exists()


def test_written_seed_matches_fingerprint(floor_md, predecessors, cache_dir):
    """Refined seed cache is amarrado to base predecessors; different base → None."""
    cache_path = Path(cache_dir) / "cache.json"

    # Rewriter: tag a thread
    def fake_rewriter(prompt):
        return floor_md.replace("- thread-1", "- [inflight] thread-1")

    result = refine_seed(
        cache_path=str(cache_path),
        predecessors=predecessors,
        floor_md=floor_md,
        rewriter=fake_rewriter,
        owner_model="gemma",
        generated_at="2026-06-30T00:00:00Z",
        exchange="",
    )
    assert result is True

    # Read with correct fingerprint (must pass same owner_model and prompt_version as refine_seed)
    fp_correct = compute_base_fingerprint(predecessors, owner_model="gemma", prompt_version="v3")
    cached_md = read_valid_refined_seed(str(cache_path), fp_correct)
    assert cached_md is not None

    # Read with different (stale) fingerprint (same model/prompt_version, different predecessors)
    different_predecessors = [
        ("chat-2", "different doc"),
        ("chat-3", "more different"),
    ]
    fp_different = compute_base_fingerprint(different_predecessors, owner_model="gemma", prompt_version="v3")
    cached_md_stale = read_valid_refined_seed(str(cache_path), fp_different)
    assert cached_md_stale is None  # Stale cache ignored
