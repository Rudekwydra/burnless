"""Tests for pipeline_state — toggle, statusline, turn counter."""
from pathlib import Path
from burnless import pipeline_state


def test_inactive_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_state, "STATE_DIR", tmp_path / "state")
    assert pipeline_state.is_active(Path("/some/project")) is False
    assert pipeline_state.read_state(Path("/some/project")) is None
    assert pipeline_state.statusline(Path("/some/project")) == ""


def test_activate_creates_state(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_state, "STATE_DIR", tmp_path / "state")
    project = Path("/some/project")
    payload = pipeline_state.activate(project, compression_mode="balanced")
    assert payload["compression_mode"] == "balanced"
    assert payload["turn_count"] == 0
    assert pipeline_state.is_active(project)


def test_deactivate_removes_state(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_state, "STATE_DIR", tmp_path / "state")
    project = Path("/p")
    pipeline_state.activate(project)
    assert pipeline_state.deactivate(project) is True
    assert pipeline_state.is_active(project) is False
    assert pipeline_state.deactivate(project) is False


def test_increment_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_state, "STATE_DIR", tmp_path / "state")
    project = Path("/p")
    pipeline_state.activate(project)
    assert pipeline_state.increment_turn(project) == 1
    assert pipeline_state.increment_turn(project) == 2
    assert pipeline_state.read_state(project)["turn_count"] == 2


def test_increment_turn_inactive_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_state, "STATE_DIR", tmp_path / "state")
    assert pipeline_state.increment_turn(Path("/p")) == 0


def test_statusline_with_turns(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_state, "STATE_DIR", tmp_path / "state")
    project = Path("/p")
    pipeline_state.activate(project, compression_mode="tight")
    pipeline_state.increment_turn(project)
    line = pipeline_state.statusline(project)
    assert "burnless pipeline ON" in line
    assert "mode=tight" in line
    assert "turns=1" in line


def test_statusline_suggests_clear_every_25(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_state, "STATE_DIR", tmp_path / "state")
    project = Path("/p")
    pipeline_state.activate(project)
    for _ in range(25):
        pipeline_state.increment_turn(project)
    line = pipeline_state.statusline(project)
    assert "consider /clear" in line


def test_project_hash_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_state, "STATE_DIR", tmp_path / "state")
    a = pipeline_state._project_key(Path("/abc"))
    b = pipeline_state._project_key(Path("/abc"))
    c = pipeline_state._project_key(Path("/def"))
    assert a == b
    assert a != c
    assert len(a) == 12
