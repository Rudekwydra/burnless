import os
import tempfile
from pathlib import Path

import pytest

from burnless.epochs import carry_forward_chain, build_refine_owner_candidates
from burnless import epochs_v2


@pytest.fixture
def temp_project_root():
    """Create a temporary project root with .burnless structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        epochs_dir = root / ".burnless" / "epochs"
        epochs_dir.mkdir(parents=True, exist_ok=True)
        yield root


def _write_living_doc(root, chat_id, content):
    """Write a living-doc v2 to the predecessor directory."""
    chat_dir = root / ".burnless" / "epochs" / chat_id
    chat_dir.mkdir(parents=True, exist_ok=True)
    living_file = epochs_v2.living_path(root, chat_id)
    living_file.parent.mkdir(parents=True, exist_ok=True)
    living_file.write_text(content, encoding='utf-8')


def test_floor_drops_contract_placeholder(temp_project_root):
    """Verify that placeholder contract entries (d000: <contrato>) are dropped."""
    os.environ["BURNLESS_EPOCH_V2"] = "1"

    # Create two predecessor living-docs with contract placeholders
    doc1 = """## Foco atual
- Tarefa 1

## Contracts
- d000: <contrato>
- d001: [contrato da segunda thread]
- utils.ts:42 export function processData(x: number): string
"""
    doc2 = """## Foco atual
- Tarefa 2

## Contracts
- d000: <contrato>
"""

    _write_living_doc(temp_project_root, "chat_001", doc1)
    _write_living_doc(temp_project_root, "chat_002", doc2)

    result = carry_forward_chain(temp_project_root)

    # Placeholders must not appear in result
    assert "d000: <contrato>" not in result
    assert "d001: [contrato" not in result
    # Real contract entry must remain
    assert "utils.ts:42" in result

    del os.environ["BURNLESS_EPOCH_V2"]


def test_floor_caps_foco_atual(temp_project_root):
    """Verify that Foco atual is capped to 3 most recent entries."""
    os.environ["BURNLESS_EPOCH_V2"] = "1"

    # Create 5 predecessor docs, each with one distinct Foco atual entry
    for i in range(1, 6):
        doc = f"""## Foco atual
- Foco sessao {i}

## Threads abertas
- thread-{i}
"""
        _write_living_doc(temp_project_root, f"chat_{i:03d}", doc)

    result = carry_forward_chain(temp_project_root)

    # Count "Foco sessao" entries in Foco atual section
    lines = result.split('\n')
    foco_idx = -1
    foco_count = 0
    for i, line in enumerate(lines):
        if line.strip() == "## Foco atual":
            foco_idx = i
            break

    if foco_idx >= 0:
        # Count entries after ## Foco atual until next section
        for i in range(foco_idx + 1, len(lines)):
            if lines[i].startswith("## "):
                break
            if "Foco sessao" in lines[i]:
                foco_count += 1

    # Should be capped to 3
    assert foco_count <= 3, f"Expected max 3 Foco atual entries, got {foco_count}"
    # All 5 threads should still be present (no cap on other sections)
    assert "thread-1" in result and "thread-5" in result

    del os.environ["BURNLESS_EPOCH_V2"]


def test_floor_keeps_real_entries(temp_project_root):
    """Verify that normal (non-placeholder) entries are preserved."""
    os.environ["BURNLESS_EPOCH_V2"] = "1"

    doc = """## Foco atual
- Implementar API de autenticação

## Decisões
- Usar JWT com expiry de 24h
- Validar CORS em staging

## Threads abertas
- #432: bug em validacao de email
- #445: docs para novo endpoint

## Refs
- commit abc123: auth module refactor
- issue #123: compliance checklist

## Contracts
- auth/login.ts:15 POST /api/auth/login(credentials) -> {token, refreshToken}
"""

    _write_living_doc(temp_project_root, "chat_main", doc)

    result = carry_forward_chain(temp_project_root)

    # All real entries must be preserved
    assert "Implementar API de autenticação" in result
    assert "Usar JWT com expiry de 24h" in result
    assert "Validar CORS em staging" in result
    assert "#432: bug em validacao de email" in result
    assert "#445: docs para novo endpoint" in result
    assert "commit abc123" in result
    assert "issue #123" in result
    assert "auth/login.ts:15" in result

    del os.environ["BURNLESS_EPOCH_V2"]
