import pytest
from pathlib import Path
import os
from burnless.epochs import resolve_root, freshest_project_root, _detect_from_transcript, carry_forward_chain


def test_resolve_subproject_without_config(tmp_path):
    """cwd = tmp_path/OneGlanse/src (no config anywhere), workspace = tmp_path
    → resolve_root returns tmp_path/OneGlanse"""
    cwd = tmp_path / "OneGlanse" / "src"
    workspace = tmp_path
    result = resolve_root(cwd, workspace=workspace)
    assert result == tmp_path / "OneGlanse"


def test_resolve_prefers_config_ancestor(tmp_path):
    """create tmp_path/Proj/.burnless/config.yaml
    cwd = tmp_path/Proj/sub, workspace = tmp_path
    → returns tmp_path/Proj"""
    proj = tmp_path / "Proj"
    (proj / ".burnless").mkdir(parents=True)
    (proj / ".burnless" / "config.yaml").write_text("test: 1")
    cwd = proj / "sub"
    workspace = tmp_path
    result = resolve_root(cwd, workspace=workspace)
    assert result == proj


def test_resolve_bare_workspace_freshest(tmp_path):
    """create two projects each with .burnless/epochs/_rolling/seed.md
    set distinct mtimes; resolve_root(workspace, workspace=workspace)
    → returns project with NEWER seed"""
    proj1 = tmp_path / "Proj1"
    proj2 = tmp_path / "Proj2"

    (proj1 / ".burnless" / "epochs" / "_rolling").mkdir(parents=True)
    (proj2 / ".burnless" / "epochs" / "_rolling").mkdir(parents=True)

    seed1 = proj1 / ".burnless" / "epochs" / "_rolling" / "seed.md"
    seed2 = proj2 / ".burnless" / "epochs" / "_rolling" / "seed.md"

    seed1.write_text("old seed")
    seed2.write_text("new seed")

    os.utime(str(seed1), (1000, 1000))
    os.utime(str(seed2), (2000, 2000))

    result = resolve_root(tmp_path, workspace=tmp_path)
    assert result == proj2


def test_carry_forward_prefers_predecessor_chat(tmp_path):
    """write root/.burnless/epochs/<oldsid>/001.md
    carry_forward_chain(root, current_chat_id="newsid")
    → returns non-empty string containing old frame text"""
    root = tmp_path / "proj"
    oldsid = "old-session-id"
    newsid = "new-session-id"

    epochs_dir = root / ".burnless" / "epochs" / oldsid
    epochs_dir.mkdir(parents=True)

    (epochs_dir / "001.md").write_text("old frame content")
    (root / ".burnless" / "config.yaml").write_text("")

    result = carry_forward_chain(root, current_chat_id=newsid)
    assert result
    assert "old frame content" in result


def test_carry_forward_seed_fallback(tmp_path):
    """only root/.burnless/epochs/_rolling/seed.md exists (non-empty)
    carry_forward_chain(root, "newsid")
    → returns its text"""
    root = tmp_path / "proj"
    seed_dir = root / ".burnless" / "epochs" / "_rolling"
    seed_dir.mkdir(parents=True)

    seed_text = "rolling seed content"
    seed_file = seed_dir / "seed.md"
    seed_file.write_text(seed_text)
    (root / ".burnless" / "config.yaml").write_text("")

    result = carry_forward_chain(root, "newsid")
    assert result == seed_text


def test_resolve_ignores_stray_home_config(tmp_path, monkeypatch):
    """A stray .burnless/config.yaml directly at Path.home() (e.g. from an
    accidental `burnless init` run in $HOME) must NOT make resolve_root
    treat home as its own project root -- home is the global state bucket,
    not a project. Falls through to the freshest real project instead."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".burnless").mkdir(parents=True)
    (tmp_path / ".burnless" / "config.yaml").write_text("project_name: Project")

    proj = tmp_path / "RealProj"
    (proj / ".burnless" / "epochs" / "_rolling").mkdir(parents=True)
    (proj / ".burnless" / "epochs" / "_rolling" / "seed.md").write_text("real seed")

    result = resolve_root(tmp_path, workspace=tmp_path)
    assert result == proj
