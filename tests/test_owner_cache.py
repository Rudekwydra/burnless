import json
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from burnless.owner_cache import (
    compute_base_fingerprint,
    write_refined_seed,
    read_valid_refined_seed,
)


def test_fingerprint_deterministic():
    """Same predecessors list -> same digest on repeated calls."""
    pred = [("chat_a", "doc_x"), ("chat_b", "doc_y")]
    fp1 = compute_base_fingerprint(pred)
    fp2 = compute_base_fingerprint(pred)
    assert fp1 == fp2
    assert isinstance(fp1, str)
    assert len(fp1) == 64  # SHA256 hex


def test_fingerprint_order_sensitive():
    """Reordering predecessors changes digest."""
    pred1 = [("chat_a", "doc_x"), ("chat_b", "doc_y")]
    pred2 = [("chat_b", "doc_y"), ("chat_a", "doc_x")]
    fp1 = compute_base_fingerprint(pred1)
    fp2 = compute_base_fingerprint(pred2)
    assert fp1 != fp2, "Order must matter"


def test_fingerprint_content_sensitive():
    """Changing one char in living_doc_text changes digest."""
    pred1 = [("chat_a", "doc_x")]
    pred2 = [("chat_a", "doc_x_modified")]
    fp1 = compute_base_fingerprint(pred1)
    fp2 = compute_base_fingerprint(pred2)
    assert fp1 != fp2, "Content must matter"


def test_roundtrip_valid():
    """write then read with same fingerprint returns original seed_md."""
    with TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "cache.json"
        pred = [("c1", "text1"), ("c2", "text2")]
        fp = compute_base_fingerprint(pred)
        seed_content = "# Refined seed\nSection 1"
        
        write_refined_seed(
            str(cache_path),
            seed_md=seed_content,
            fingerprint=fp,
            owner_model="gpt-5.5",
            generated_at="2026-06-29T00:00:00Z",
        )
        
        read_seed = read_valid_refined_seed(str(cache_path), fp)
        assert read_seed == seed_content


def test_read_stale_returns_none():
    """Read with different fingerprint returns None (cache stale)."""
    with TemporaryDirectory() as tmpdir:
        cache_path = Path(tmpdir) / "cache.json"
        pred = [("c1", "text1")]
        fp_original = compute_base_fingerprint(pred)
        
        write_refined_seed(
            str(cache_path),
            seed_md="old seed",
            fingerprint=fp_original,
            owner_model="gpt-5.5",
            generated_at="2026-06-29T00:00:00Z",
        )
        
        pred_modified = [("c1", "text1_modified")]
        fp_new = compute_base_fingerprint(pred_modified)
        assert fp_new != fp_original
        
        read_seed = read_valid_refined_seed(str(cache_path), fp_new)
        assert read_seed is None


def test_read_missing_returns_none():
    """Read from nonexistent file returns None (no exception)."""
    cache_path = "/nonexistent/path/cache.json"
    read_seed = read_valid_refined_seed(cache_path, "any_fingerprint")
    assert read_seed is None


def test_fingerprint_model_sensitive():
    """Changing owner_model changes digest."""
    pred = [("c1", "x")]
    fp1 = compute_base_fingerprint(pred, owner_model="haiku")
    fp2 = compute_base_fingerprint(pred, owner_model="gemma")
    assert fp1 != fp2, "owner_model must affect digest"


def test_fingerprint_prompt_version_sensitive():
    """Changing prompt_version changes digest."""
    pred = [("c1", "x")]
    fp1 = compute_base_fingerprint(pred, prompt_version="v3")
    fp2 = compute_base_fingerprint(pred, prompt_version="v4")
    assert fp1 != fp2, "prompt_version must affect digest"


def test_fingerprint_backcompat_default():
    """Calling with defaults only (no model/prompt_version) is deterministic and stable."""
    pred = [("c1", "x")]
    fp1 = compute_base_fingerprint(pred)
    fp2 = compute_base_fingerprint(pred)
    assert fp1 == fp2
    assert len(fp1) == 64