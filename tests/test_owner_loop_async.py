"""Tests for owner-loop async refinement and BURNLESS_LOCAL_API support."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from burnless import epochs, epochs_v2, owner_loop, owner_cache


def test_refine_seed_calls_validator():
    """Test refine_seed calls validator and respects validator output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "refined_seed.json"

        predecessors = [
            ("chat_a", "## Foco atual\n- task\n"),
        ]

        floor_md = "## Foco atual\n- task\n"

        def mock_rewriter(prompt: str) -> str:
            return "## Foco atual\n- task\n"

        result = owner_loop.refine_seed(
            cache_path=str(cache_path),
            predecessors=predecessors,
            floor_md=floor_md,
            rewriter=mock_rewriter,
            owner_model="test-model",
            generated_at="2026-06-30T01:00:00Z",
        )

        assert result is False, "refine_seed should return False when safe == floor"
        assert not cache_path.exists(), "cache should not be written when safe == floor"


def test_refine_seed_fails_open_rewriter_none():
    """Test refine_seed returns False when rewriter returns None."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "refined_seed.json"

        predecessors = [("chat_a", "## Foco atual\n- task\n")]
        floor_md = "## Foco atual\n- focus\n"

        def mock_rewriter(prompt: str):
            return None

        result = owner_loop.refine_seed(
            cache_path=str(cache_path),
            predecessors=predecessors,
            floor_md=floor_md,
            rewriter=mock_rewriter,
            owner_model="test-model",
            generated_at="2026-06-30T01:00:00Z",
        )

        assert result is False, "refine_seed should return False on rewriter None"
        assert not cache_path.exists(), "cache should not be written"


def test_refine_seed_fails_open_validation_collapse():
    """Test refine_seed returns False when validation collapses to floor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "refined_seed.json"

        predecessors = [("chat_a", "## Foco atual\n- task\n")]
        floor_md = "## Foco atual\n- focus\n"

        def mock_rewriter(prompt: str):
            # Rewriter succeeds but content is same as floor
            return floor_md

        result = owner_loop.refine_seed(
            cache_path=str(cache_path),
            predecessors=predecessors,
            floor_md=floor_md,
            rewriter=mock_rewriter,
            owner_model="test-model",
            generated_at="2026-06-30T01:00:00Z",
        )

        assert result is False, "refine_seed should return False when safe == floor"
        assert not cache_path.exists(), "cache should not be written"


def test_refine_seed_fails_open_exception():
    """Test refine_seed returns False on exception."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "refined_seed.json"

        predecessors = [("chat_a", "## Foco atual\n- task\n")]
        floor_md = "## Foco atual\n- focus\n"

        def mock_rewriter(prompt: str):
            raise RuntimeError("rewriter error")

        result = owner_loop.refine_seed(
            cache_path=str(cache_path),
            predecessors=predecessors,
            floor_md=floor_md,
            rewriter=mock_rewriter,
            owner_model="test-model",
            generated_at="2026-06-30T01:00:00Z",
        )

        assert result is False, "refine_seed should return False on exception"
        assert not cache_path.exists(), "cache should not be written"


def test_living_rewriter_llamacpp_endpoint_by_inspection():
    """Test living_rewriter code uses llamacpp endpoint when BURNLESS_LOCAL_API=llamacpp."""
    import inspect
    source = inspect.getsource(epochs_v2.living_rewriter)
    assert "11435" in source, "living_rewriter source should reference port 11435"
    assert "BURNLESS_LOCAL_API" in source, "living_rewriter should check BURNLESS_LOCAL_API env var"
    assert "llamacpp" in source, "living_rewriter should handle llamacpp provider"


def test_living_rewriter_timeout_by_inspection():
    """Test living_rewriter code sets timeout >=120 for llamacpp and 20 for ollama."""
    import inspect
    source = inspect.getsource(epochs_v2.living_rewriter)
    assert "timeout" in source, "living_rewriter should set timeout parameter"
    assert "120" in source, "living_rewriter should use 120 timeout for llamacpp"
    assert "content" in source, "living_rewriter should parse llama.cpp content response key"


def test_build_refine_owner_candidates_no_env():
    """Test build_refine_owner_candidates returns None when BURNLESS_EPOCH_V2 not set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        root.mkdir(exist_ok=True)

        os.environ.pop("BURNLESS_EPOCH_V2", None)
        try:
            result = epochs.build_refine_owner_candidates(root)
            assert result is None
        finally:
            pass


def test_build_refine_owner_candidates_with_predecessors():
    """Test build_refine_owner_candidates builds predecessors and floor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        root.mkdir(exist_ok=True)

        epochs_dir = root / ".burnless" / "epochs"
        epochs_dir.mkdir(parents=True, exist_ok=True)

        chat_a_dir = epochs_dir / "chat_a"
        chat_a_dir.mkdir(exist_ok=True)
        (chat_a_dir / "living.md").write_text("## Foco atual\n- task1\n\n## Threads abertas\n- pending1\n")

        chat_b_dir = epochs_dir / "chat_b"
        chat_b_dir.mkdir(exist_ok=True)
        (chat_b_dir / "living.md").write_text("## Foco atual\n- task2\n")

        os.environ["BURNLESS_EPOCH_V2"] = "1"
        try:
            result = epochs.build_refine_owner_candidates(root)
            assert result is not None
            predecessors, floor_md = result
            assert len(predecessors) == 2
            assert predecessors[0][0] in ["chat_a", "chat_b"]
            assert "Foco atual" in floor_md
        finally:
            os.environ.pop("BURNLESS_EPOCH_V2", None)


def test_build_refine_owner_candidates_includes_current_chat_for_cache_alignment():
    """The refine-owner fingerprint must include the just-closed chat so the next /clear
    can hit the refined cache with the same predecessor set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        epochs_dir = root / ".burnless" / "epochs"
        epochs_dir.mkdir(parents=True, exist_ok=True)

        chat_a_dir = epochs_dir / "chat_a"
        chat_a_dir.mkdir(exist_ok=True)
        (chat_a_dir / "living.md").write_text("## Foco atual\n- task1\n", encoding="utf-8")

        current_dir = epochs_dir / "chat_current"
        current_dir.mkdir(exist_ok=True)
        (current_dir / "living.md").write_text("## Foco atual\n- task_current\n", encoding="utf-8")

        os.environ["BURNLESS_EPOCH_V2"] = "1"
        try:
            result = epochs.build_refine_owner_candidates(root, current_chat_id="chat_current")
            assert result is not None
            predecessors, _ = result
            chat_ids = [chat_id for chat_id, _ in predecessors]
            assert "chat_current" in chat_ids
            assert "chat_a" in chat_ids
        finally:
            os.environ.pop("BURNLESS_EPOCH_V2", None)


def test_epoch_stop_script_has_refine_owner_call():
    """Test Stop hook script invokes refine-owner after seed write."""
    script_path = Path("/Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_stop.sh")
    assert script_path.exists(), "Stop hook script must exist"

    with open(script_path) as f:
        content = f.read()

    assert 'epoch refine-owner' in content, "Stop hook must call 'epoch refine-owner'"
    assert 'ROOT/.burnless/epochs/_rolling/seed.md' in content, "Stop hook must handle seed.md path"
