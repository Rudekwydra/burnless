"""Tests for EN/PT marker transformation (B2)."""

import json
import tempfile
from pathlib import Path

import pytest

from burnless.markers import to_en_markers, to_pt_markers, SECTION_PT_TO_EN
from burnless.recovery import _format_en_markers, _render_pending_block, _render_v3_sections, write_checkpoint, read_checkpoint


# PT doc with full 8-section structure and Q/A block
FULL_PT_DOC = """## Foco atual
- focus item 1
- focus item 2

## Threads abertas
- thread 1
- thread 2

## Decisões
- decision 1
- decision 2

## Contracts
- contract 1

## Refs
- /path/to/file.py — why [seq 1]

## Riscos
- risk 1

## Última validação
- validation note

## Recuperáveis
- d123 — recoverable note [seq 1]

PERGUNTA:
user asked something

RESPOSTA:
assistant responded something
"""


def test_roundtrip_pt_en_pt():
    """Roundtrip: PT → EN → PT equals original."""
    en_doc = to_en_markers(FULL_PT_DOC)
    pt_doc_recovered = to_pt_markers(en_doc)
    assert pt_doc_recovered == FULL_PT_DOC

    # EN version has EN markers
    assert "## Current focus" in en_doc
    assert "## Decisions" in en_doc
    assert "Q:" in en_doc
    assert "A:" in en_doc
    # EN version has no PT headers
    assert "## Foco atual" not in en_doc
    assert "## Decisões" not in en_doc
    assert "PERGUNTA:" not in en_doc
    assert "RESPOSTA:" not in en_doc


def test_to_en_markers_leaves_content_untouched():
    """Content mentioning PT keywords mid-line stays intact."""
    doc = """## Foco atual
- fala de Decisões aqui

PERGUNTA:
something about PERGUNTA in context

RESPOSTA:
answer
"""
    en_doc = to_en_markers(doc)

    # Structural markers transformed
    assert "## Current focus" in en_doc
    assert "Q:" in en_doc
    assert "A:" in en_doc

    # Content preserved
    assert "Decisões" in en_doc  # mid-line mention
    assert "PERGUNTA in context" in en_doc  # mid-line mention


def test_write_checkpoint_en_toggle_on(tmp_path):
    """With format.en_markers=true, checkpoint has EN markers + format_version=2."""
    root = tmp_path / "project"
    root.mkdir()
    burnless_dir = root / ".burnless"
    burnless_dir.mkdir()

    # Write config with EN toggle
    config_file = burnless_dir / "config.yaml"
    config_file.write_text("format:\n  en_markers: true\n", encoding="utf-8")

    pt_living_md = """## Foco atual
- x

## Decisões
- y

PERGUNTA:
q

RESPOSTA:
a
"""

    payload = write_checkpoint(
        root,
        host="claude",
        host_session_id="test_session",
        process_instance_id="test_proc",
        living_md=pt_living_md,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    # Check payload has format_version
    assert payload.get("format_version") == 2
    # Check checkpoint file on disk has EN markers
    checkpoint_file = burnless_dir / "epochs" / "sessions" / "claude" / "test_session" / "checkpoint.json"
    assert checkpoint_file.exists()

    raw_data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert "## Current focus" in raw_data["living_md"]
    assert "## Foco atual" not in raw_data["living_md"]
    assert raw_data["format_version"] == 2


def test_write_checkpoint_toggle_off_unchanged(tmp_path):
    """Without EN toggle (or toggle=false), checkpoint is PT-only, no format_version."""
    root = tmp_path / "project"
    root.mkdir()
    burnless_dir = root / ".burnless"
    burnless_dir.mkdir()

    # No config file or explicit toggle=false
    pt_living_md = """## Foco atual
- x

## Decisões
- y
"""

    payload = write_checkpoint(
        root,
        host="claude",
        host_session_id="test_session",
        process_instance_id="test_proc",
        living_md=pt_living_md,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    # No format_version key
    assert "format_version" not in payload

    # Checkpoint file has PT markers
    checkpoint_file = burnless_dir / "epochs" / "sessions" / "claude" / "test_session" / "checkpoint.json"
    raw_data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert "## Foco atual" in raw_data["living_md"]
    assert "## Current focus" not in raw_data["living_md"]
    assert "format_version" not in raw_data


def test_read_checkpoint_normalizes_to_pt(tmp_path):
    """read_checkpoint normalizes EN markers back to PT."""
    root = tmp_path / "project"
    root.mkdir()
    burnless_dir = root / ".burnless"
    burnless_dir.mkdir()

    # Write config with EN toggle
    config_file = burnless_dir / "config.yaml"
    config_file.write_text("format:\n  en_markers: true\n", encoding="utf-8")

    pt_living_md = """## Foco atual
- x
"""

    # Write checkpoint (will be EN)
    write_checkpoint(
        root,
        host="claude",
        host_session_id="test_session",
        process_instance_id="test_proc",
        living_md=pt_living_md,
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    # Read it back
    read_data = read_checkpoint(root, host="claude", host_session_id="test_session")
    assert read_data is not None

    # living_md is normalized to PT
    assert "## Foco atual" in read_data["living_md"]
    assert "## Current focus" not in read_data["living_md"]


def test_render_pending_block_en():
    """_render_pending_block(en=True) emits Q:/A:; default emits PERGUNTA:/RESPOSTA:."""
    record = {
        "seq": 1,
        "exchange_id": "ex-123",
        "user_text": "user message",
        "assistant_text": "assistant message",
        "files": ["file1.py", "file2.py"],
    }

    # Default (PT)
    pt_block = _render_pending_block(record)
    assert "PERGUNTA:" in pt_block
    assert "RESPOSTA:" in pt_block
    assert "\nQ:\n" not in pt_block
    assert "\nA:\n" not in pt_block

    # EN mode
    en_block = _render_pending_block(record, en=True)
    assert "\nQ:\n" in en_block
    assert "\nA:\n" in en_block
    assert "PERGUNTA:" not in en_block
    assert "RESPOSTA:" not in en_block


def test_render_v3_sections_en():
    """_render_v3_sections(en=True) uses EN section names."""
    parsed = {
        "Foco atual": ["item 1", "item 2"],
        "Decisões": ["decision 1"],
    }

    # Default (PT)
    pt_output = _render_v3_sections(parsed, ("Foco atual", "Decisões"))
    assert "## Foco atual" in pt_output
    assert "## Decisões" in pt_output
    assert "## Current focus" not in pt_output
    assert "## Decisions" not in pt_output

    # EN mode
    en_output = _render_v3_sections(parsed, ("Foco atual", "Decisões"), en=True)
    assert "## Current focus" in en_output
    assert "## Decisions" in en_output
    assert "## Foco atual" not in en_output
    assert "## Decisões" not in en_output
