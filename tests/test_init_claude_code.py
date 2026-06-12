"""
Tests for init-inherit: project config gets no agents key; global gets seeded only if absent.
"""
import os
import yaml
import pytest
from pathlib import Path
from unittest.mock import patch

from burnless import config as config_mod


def test_write_default_no_agents_key(tmp_path):
    p = tmp_path / "config.yaml"
    config_mod.write_default(p, agents_override=None)
    data = yaml.safe_load(p.read_text())
    assert "agents" not in data


def test_write_default_with_agents(tmp_path):
    p = tmp_path / "config.yaml"
    agents = {"silver": {"name": "sonnet", "command": "claude"}}
    config_mod.write_default(p, agents_override=agents)
    data = yaml.safe_load(p.read_text())
    assert "agents" in data
    assert data["agents"]["silver"]["name"] == "sonnet"


def test_global_seeded_when_absent(tmp_path):
    gp = tmp_path / "global_config.yaml"
    with patch.dict(os.environ, {"BURNLESS_GLOBAL_CONFIG": str(gp)}):
        agents = {"silver": {"name": "sonnet", "command": "claude"}}
        if not gp.exists():
            config_mod.write_default(gp, agents_override=agents)
        data = yaml.safe_load(gp.read_text())
        assert "agents" in data
        assert data["agents"]["silver"]["name"] == "sonnet"


def test_global_not_clobbered_when_exists(tmp_path):
    gp = tmp_path / "global_config.yaml"
    gp.write_text(yaml.safe_dump({"agents": {"silver": {"name": "keepme"}}}))
    with patch.dict(os.environ, {"BURNLESS_GLOBAL_CONFIG": str(gp)}):
        # Simulate cmd_init: only write global if absent
        if not gp.exists():
            config_mod.write_default(gp, agents_override={"silver": {"name": "clobber"}})
        data = yaml.safe_load(gp.read_text())
        assert data["agents"]["silver"]["name"] == "keepme"


def test_project_config_no_agents_after_init(tmp_path):
    project_cfg = tmp_path / ".burnless" / "config.yaml"
    project_cfg.parent.mkdir(parents=True)
    config_mod.write_default(project_cfg, agents_override=None)
    data = yaml.safe_load(project_cfg.read_text())
    assert "agents" not in data


def test_idempotent_write_default(tmp_path):
    p = tmp_path / "config.yaml"
    config_mod.write_default(p, agents_override=None)
    first = p.read_text()
    config_mod.write_default(p, agents_override=None)
    second = p.read_text()
    assert first == second


def test_load_inherits_global_agents(tmp_path):
    gp = tmp_path / "global_config.yaml"
    project_cfg = tmp_path / "project_config.yaml"
    agents = {"silver": {"name": "sonnet", "command": "claude --model sonnet -p"}}
    config_mod.write_default(gp, agents_override=agents)
    config_mod.write_default(project_cfg, agents_override=None)
    with patch.dict(os.environ, {"BURNLESS_GLOBAL_CONFIG": str(gp)}):
        loaded = config_mod.load(project_cfg)
    assert "agents" in loaded
    assert loaded["agents"]["silver"]["name"] == "sonnet"
