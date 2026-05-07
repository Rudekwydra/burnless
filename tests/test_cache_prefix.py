"""QTP-F: cacheable prefix layout opt-in."""
from __future__ import annotations

from pathlib import Path

from burnless.cli import _with_runtime_context, _build_cacheable_runtime_prefix


def test_legacy_layout_when_cache_prefix_false(tmp_path: Path):
    """Default: task first, runtime context after."""
    out = _with_runtime_context(
        "do thing X",
        project_root=tmp_path,
        burnless_root=tmp_path / ".burnless",
        chain=None,
        cache_prefix=False,
    )
    task_idx = out.find("do thing X")
    runtime_idx = out.find("Burnless Runtime Context")
    assert task_idx >= 0 and runtime_idx >= 0
    assert task_idx < runtime_idx, "legacy: task before runtime"


def test_qtp_f_layout_when_cache_prefix_true(tmp_path: Path):
    """QTP-F: runtime first (cacheable), task in middle, suffix at end."""
    out = _with_runtime_context(
        "do thing X",
        project_root=tmp_path,
        burnless_root=tmp_path / ".burnless",
        chain=None,
        cache_prefix=True,
    )
    runtime_idx = out.find("Burnless Runtime Context")
    task_idx = out.find("do thing X")
    suffix_idx = out.find("Output contract")
    assert runtime_idx >= 0 and task_idx >= 0 and suffix_idx >= 0
    assert runtime_idx < task_idx < suffix_idx, "QTP-F: runtime → task → suffix"


def test_qtp_f_with_chain(tmp_path: Path):
    """When chain capsules exist, manifest goes between task and suffix."""
    burnless = tmp_path / ".burnless"
    (burnless / "capsules").mkdir(parents=True)
    (burnless / "capsules" / "d042.json").write_text('{"id":"d042"}')
    out = _with_runtime_context(
        "task body",
        project_root=tmp_path,
        burnless_root=burnless,
        chain=["d042"],
        cache_prefix=True,
    )
    runtime_idx = out.find("Burnless Runtime Context")
    task_idx = out.find("task body")
    manifest_idx = out.find("Lazy Context Manifest")
    suffix_idx = out.find("Output contract")
    assert runtime_idx < task_idx < manifest_idx < suffix_idx


def test_runtime_prefix_stable_across_calls(tmp_path: Path):
    """Same project_root + burnless_root → identical prefix bytes."""
    a = _build_cacheable_runtime_prefix(tmp_path, tmp_path / ".burnless")
    b = _build_cacheable_runtime_prefix(tmp_path, tmp_path / ".burnless")
    assert a == b


def test_runtime_prefix_changes_with_memory_index(tmp_path: Path):
    """Memory index presence flips the hint message."""
    burnless = tmp_path / ".burnless"
    burnless.mkdir()
    a = _build_cacheable_runtime_prefix(tmp_path, burnless)
    (burnless / "memories").mkdir()
    (burnless / "memories" / "index.json").write_text("{}")
    b = _build_cacheable_runtime_prefix(tmp_path, burnless)
    assert a != b
    assert "not created yet" in a
    assert "not created yet" not in b


def test_default_cache_prefix_disabled():
    """Default config: cache_prefix.enabled is False (backwards compat)."""
    from burnless import config
    assert config.DEFAULT_CONFIG["cache_prefix"]["enabled"] is False


def test_cache_prefix_layout_includes_output_contract(tmp_path: Path):
    out = _with_runtime_context(
        "x",
        project_root=tmp_path,
        burnless_root=tmp_path / ".burnless",
        chain=None,
        cache_prefix=True,
    )
    assert "Output contract" in out
    assert "OK|PART|ERR|BLK" in out
