import pytest
import os
from pathlib import Path
from unittest.mock import patch
from burnless.epochs import carry_forward_chain, epoch_dir


LIVING_5SECTIONS = """## Foco atual
old debugless focus

## Threads abertas
- thread A

## Decisões
- decision X

## Contracts
- contract Y

## Refs
- ref Z
"""

RECONCILED_MARKDOWN = """## Foco atual
old debugless focus + 6A done report shipped

## Threads abertas
- thread A

## Decisões
- decision X (implemented via commits)
6A done report shipped

## Contracts
- contract Y

## Refs
- ref Z
"""

MOCKED_COMMITS = "\n## Commits apos o checkpoint (reconciliar vs Threads abertas)\n- abc123 feat: did the thing\n"


@pytest.fixture
def setup_v2_env(tmp_path, monkeypatch):
    """Setup tmp_path with V2 environment: .burnless/epochs/<pred>/living.md"""
    monkeypatch.setenv("BURNLESS_EPOCH_V2", "1")

    # Create predecessor chat with living.md
    pred_dir = epoch_dir(tmp_path, "predecessor")
    pred_dir.mkdir(parents=True, exist_ok=True)
    living_file = pred_dir / "living.md"
    living_file.write_text(LIVING_5SECTIONS, encoding="utf-8")

    return tmp_path


def test_default_mode_raw_appends_commits(setup_v2_env, monkeypatch):
    """No epoch.resume_recon in config => raw append, no living:reconciled"""
    tmp_path = setup_v2_env

    # Monkeypatch _commits_since_mtime to return fixed commits
    with patch("burnless.epochs._commits_since_mtime", return_value=MOCKED_COMMITS):
        result = carry_forward_chain(tmp_path, current_chat_id="someother")

    # Assert raw commits appear
    assert "abc123 feat: did the thing" in result
    # Assert no reconciliation marker
    assert "living:reconciled" not in result


def test_semantic_failopen_when_no_model(setup_v2_env, monkeypatch):
    """Config says semantic but encoder=passthrough (no model) => fail-open to raw"""
    tmp_path = setup_v2_env

    # Create .burnless/config.yaml with semantic mode but passthrough encoder
    cfg_dir = tmp_path / ".burnless"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text(
        "epoch:\n  resume_recon: semantic\nencoder:\n  provider: passthrough\n",
        encoding="utf-8"
    )

    # Monkeypatch _commits_since_mtime to return fixed commits
    with patch("burnless.epochs._commits_since_mtime", return_value=MOCKED_COMMITS):
        result = carry_forward_chain(tmp_path, current_chat_id="someother")

    # Assert fail-open: raw commits appear
    assert "abc123 feat: did the thing" in result
    # Assert no reconciliation marker (fail-open)
    assert "living:reconciled" not in result


def test_semantic_fold_replaces_raw(setup_v2_env, monkeypatch):
    """Config says semantic, living_rewriter returns reconciled doc => fold replaces raw"""
    tmp_path = setup_v2_env

    # Create .burnless/config.yaml with semantic mode
    cfg_dir = tmp_path / ".burnless"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text("epoch:\n  resume_recon: semantic\n", encoding="utf-8")

    # Monkeypatch _commits_since_mtime and living_rewriter
    with patch("burnless.epochs._commits_since_mtime", return_value=MOCKED_COMMITS):
        with patch("burnless.epochs_v2.living_rewriter") as mock_rewriter:
            # living_rewriter returns a callable that ignores its prompt and returns canned markdown
            def mock_rewrite_func(prompt):
                return RECONCILED_MARKDOWN
            mock_rewriter.return_value = mock_rewrite_func

            result = carry_forward_chain(tmp_path, current_chat_id="someother")

    # Assert reconciliation marker is present
    assert "living:reconciled" in result
    # Assert canned text from reconciled doc is present
    assert "6A done report shipped" in result
    # Assert raw commit line is NOT present (replaced by fold)
    assert "abc123 feat: did the thing" not in result
