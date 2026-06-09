"""Cold-start cache sizing per model (Gap a)."""
import pytest

from burnless.coreconfig.resolver import min_cache_tokens
from burnless.cached_worker import build_system_blocks


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-haiku-4-5-20251001", 2048),
        ("haiku", 2048),
        ("claude-sonnet-4-6", 1024),
        ("claude-opus-4-8", 1024),
        ("", 1024),
        (None, 1024),
    ],
)
def test_min_cache_tokens(model, expected):
    assert min_cache_tokens(model) == expected


def test_build_system_blocks_model_aware_padding(tmp_path):
    haiku = build_system_blocks(
        project_root=tmp_path, burnless_root=tmp_path,
        model="claude-haiku-4-5-20251001",
    )[0]["text"]
    sonnet = build_system_blocks(
        project_root=tmp_path, burnless_root=tmp_path,
        model="claude-sonnet-4-6",
    )[0]["text"]

    # estimated tokens = len/3.5
    assert len(haiku) / 3.5 >= 2048
    assert len(sonnet) / 3.5 >= 1024
    # real content already clears both thresholds; per-model padding only differs when content < min


def test_build_system_blocks_empty_model_falls_back(tmp_path):
    # No model -> module-constant fallback (1024), same as old behavior.
    blk = build_system_blocks(project_root=tmp_path, burnless_root=tmp_path)[0]["text"]
    assert len(blk) / 3.5 >= 1024
