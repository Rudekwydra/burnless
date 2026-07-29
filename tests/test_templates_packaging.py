"""A pip-installed burnless must still find its templates.

Without them `init --claude-code` wires nothing, which is the whole
Claude Code integration. This is the regression that shipped in 0.9.5.
"""
import pytest

tomllib = pytest.importorskip("tomllib")  # stdlib so no 3.11+; CI ainda roda 3.10
from pathlib import Path

from burnless import init_claude_code

REPO = Path(__file__).resolve().parent.parent


def test_wheel_ships_the_templates_directory():
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text())
    include = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert include["templates"] == "burnless/templates"


def test_resolver_finds_templates_shipped_inside_the_package(tmp_path, monkeypatch):
    pkg = tmp_path / "site-packages" / "burnless"
    (pkg / "templates").mkdir(parents=True)
    monkeypatch.setattr(init_claude_code, "__file__", str(pkg / "init_claude_code.py"))

    assert init_claude_code._resolve_templates_dir() == pkg / "templates"


def test_resolver_returns_none_when_templates_are_truly_absent(tmp_path, monkeypatch):
    pkg = tmp_path / "site-packages" / "burnless"
    pkg.mkdir(parents=True)
    monkeypatch.setattr(init_claude_code, "__file__", str(pkg / "init_claude_code.py"))

    assert init_claude_code._resolve_templates_dir() is None
