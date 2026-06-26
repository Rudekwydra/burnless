import pytest
from pathlib import Path

from burnless.scope import stable_project_hash, project_scope, assert_same_project


def test_stable_project_hash_starts_with_sha256():
    """stable_project_hash returns 'sha256:' + hex."""
    result = stable_project_hash("/tmp")
    assert result.startswith("sha256:")
    assert len(result) == 7 + 64


def test_stable_project_hash_deterministic():
    """Same path -> same hash."""
    h1 = stable_project_hash("/tmp")
    h2 = stable_project_hash("/tmp")
    assert h1 == h2


def test_stable_project_hash_different_paths():
    """Different paths -> different hashes."""
    h1 = stable_project_hash("/tmp")
    h2 = stable_project_hash("/var")
    assert h1 != h2


def test_project_scope_absolute_path(tmp_path):
    """project_scope returns absolute project_root."""
    scope = project_scope(tmp_path)
    assert scope["project_root"].startswith("/")
    assert Path(scope["project_root"]).is_absolute()


def test_project_scope_matching_hash(tmp_path):
    """project_scope hash matches stable_project_hash."""
    scope = project_scope(tmp_path)
    expected_hash = stable_project_hash(tmp_path)
    assert scope["project_root_hash"] == expected_hash


def test_project_scope_echoes_parameters(tmp_path):
    """project_scope echoes session_id, chat_id, source."""
    scope = project_scope(
        tmp_path, session_id="s123", chat_id="c456", source="api"
    )
    assert scope["session_id"] == "s123"
    assert scope["chat_id"] == "c456"
    assert scope["source"] == "api"


def test_project_scope_defaults(tmp_path):
    """project_scope uses defaults for optional params."""
    scope = project_scope(tmp_path)
    assert scope["session_id"] is None
    assert scope["chat_id"] is None
    assert scope["source"] == "cli"


def test_assert_same_project_nested_scope_match(tmp_path):
    """assert_same_project returns True when nested scope hash matches."""
    record = {
        "scope": {"project_root_hash": stable_project_hash(tmp_path)}
    }
    assert assert_same_project(record, tmp_path) is True


def test_assert_same_project_top_level_hash_match(tmp_path):
    """assert_same_project returns True when top-level hash matches."""
    record = {"project_root_hash": stable_project_hash(tmp_path)}
    assert assert_same_project(record, tmp_path) is True


def test_assert_same_project_different_hash(tmp_path):
    """assert_same_project returns False for different project hash."""
    other_hash = stable_project_hash("/var")
    record = {"project_root_hash": other_hash}
    assert assert_same_project(record, tmp_path) is False


def test_assert_same_project_nested_scope_different(tmp_path):
    """assert_same_project returns False for different nested scope hash."""
    other_hash = stable_project_hash("/var")
    record = {"scope": {"project_root_hash": other_hash}}
    assert assert_same_project(record, tmp_path) is False


def test_assert_same_project_legacy_no_hash():
    """assert_same_project returns True for legacy record with no hash."""
    record = {"some_field": "value"}
    assert assert_same_project(record, "/tmp") is True


def test_assert_same_project_empty_record(tmp_path):
    """assert_same_project returns True for empty record (backward-compat)."""
    assert assert_same_project({}, tmp_path) is True


def test_assert_same_project_nested_scope_priority(tmp_path):
    """assert_same_project checks nested scope first, ignores top-level."""
    nested_hash = stable_project_hash(tmp_path)
    other_hash = stable_project_hash("/var")
    record = {
        "scope": {"project_root_hash": nested_hash},
        "project_root_hash": other_hash,
    }
    assert assert_same_project(record, tmp_path) is True
