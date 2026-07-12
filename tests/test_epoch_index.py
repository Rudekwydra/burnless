"""Tests for epoch INDEX.md feature."""

import pytest
from pathlib import Path
from burnless.exporting import (
    update_epoch_index,
    _export_title,
    backfill_epoch_index,
    _index_enabled,
)


def test_update_epoch_index_creates_index_with_header_and_line(tmp_path):
    """Test that update_epoch_index creates INDEX.md with header and first entry."""
    project_root = tmp_path / "myproject"
    project_root.mkdir()
    burnless_dir = project_root / ".burnless"
    burnless_dir.mkdir()

    update_epoch_index(
        burnless_dir,
        created="2026-07-12T20:00:00Z",
        host="claude",
        sid8="abc12345",
        generation="1",
        export_filename="epoch-claude-abc12345-20260712T200000Z.md",
        title="Test task",
    )

    index_path = burnless_dir / "epochs" / "INDEX.md"
    assert index_path.exists()
    content = index_path.read_text(encoding="utf-8")
    assert "# Epoch index — myproject" in content
    assert "2026-07-12T20:00:00Z" in content
    assert "Test task" in content
    assert "exports/epoch-claude-abc12345-20260712T200000Z.md" in content


def test_update_epoch_index_idempotent(tmp_path):
    """Test that calling update_epoch_index twice with same filename doesn't duplicate."""
    project_root = tmp_path / "myproject"
    project_root.mkdir()
    burnless_dir = project_root / ".burnless"
    burnless_dir.mkdir()

    for _ in range(2):
        update_epoch_index(
            burnless_dir,
            created="2026-07-12T20:00:00Z",
            host="claude",
            sid8="abc12345",
            generation="1",
            export_filename="epoch-claude-abc12345-20260712T200000Z.md",
            title="Test task",
        )

    index_path = burnless_dir / "epochs" / "INDEX.md"
    content = index_path.read_text(encoding="utf-8")
    assert content.count("2026-07-12T20:00:00Z") == 1


def test_export_title_extracts_en_pt_titles(tmp_path):
    """Test _export_title extracts titles from both English and Portuguese sections."""
    # English
    en_content = """---
schema: test
---

## Current focus
- [state] Clean up exports [chat:123]

Other text"""
    title = _export_title(en_content)
    assert title == "Clean up exports"

    # Portuguese
    pt_content = """---
schema: test
---

## Foco atual
- [inflight] Implementar feature [chat:456]

More text"""
    title = _export_title(pt_content)
    assert title == "Implementar feature"

    # Skips empty lines after header, finds next content
    empty_after_header = """---
schema: test
---

## Current focus

Other text"""
    title = _export_title(empty_after_header)
    assert title == "Other text"


def test_backfill_epoch_index_from_exports(tmp_path):
    """Test backfill_epoch_index processes existing exports."""
    project_root = tmp_path / "myproject"
    project_root.mkdir()
    burnless_dir = project_root / ".burnless"
    burnless_dir.mkdir()
    exports_dir = burnless_dir / "exports"
    exports_dir.mkdir()

    # Create 2 synthetic exports
    export1 = exports_dir / "epoch-claude-sid10001-20260712T100000Z.md"
    export1.write_text(
        """---
schema: burnless-epoch-export/v1
host: claude
host_session_id: sid10001234567890
generation: 1
created: 2026-07-12T10:00:00Z
---

## Current focus
- First task [chat:001]

Content here"""
    )

    export2 = exports_dir / "epoch-claude-sid20002-20260712T110000Z.md"
    export2.write_text(
        """---
schema: burnless-epoch-export/v1
host: claude
host_session_id: sid20002345678901
generation: 2
created: 2026-07-12T11:00:00Z
---

## Foco atual
- Second task [chat:002]

More content"""
    )

    # First backfill (pass project_root, which will be resolved to .burnless by recovery._root_path)
    result = backfill_epoch_index(project_root)
    assert result["status"] == "indexed"
    assert result["added"] == 2
    assert result["total"] == 2
    assert "index_path" in result
    assert result["index_path"].endswith("epochs/INDEX.md")
    assert Path(result["index_path"]).exists()

    index_path = burnless_dir / "epochs" / "INDEX.md"
    assert index_path.exists()
    index_content = index_path.read_text(encoding="utf-8")
    assert "First task" in index_content
    assert "Second task" in index_content

    # Second backfill should add 0
    result2 = backfill_epoch_index(project_root)
    assert result2["status"] == "indexed"
    assert result2["added"] == 0
    assert result2["total"] == 2


def test_index_enabled_defaults_false_and_respects_config(tmp_path):
    """Test _index_enabled reads from config.yaml, defaults to False."""
    project_root = tmp_path / "myproject"
    project_root.mkdir()
    burnless_dir = project_root / ".burnless"
    burnless_dir.mkdir()

    # No config.yaml → False
    assert _index_enabled(burnless_dir) is False

    # With config.yaml epochs.index: false → False
    config_false = burnless_dir / "config.yaml"
    config_false.write_text("epochs:\n  index: false\n")
    assert _index_enabled(burnless_dir) is False

    # With config.yaml epochs.index: true → True
    config_true = burnless_dir / "config.yaml"
    config_true.write_text("epochs:\n  index: true\n")
    assert _index_enabled(burnless_dir) is True
