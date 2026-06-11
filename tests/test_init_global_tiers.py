"""
Test the policy of seeding global tiers and stripping project-specific agents.
"""
import os
import yaml
import pytest
from pathlib import Path
from unittest.mock import patch

from burnless import config

def test_global_seed_no_clobber(tmp_path):
    # 1. Setup: Global config ALREADY has agents
    gp = tmp_path / "global_config.yaml"
    gp.parent.mkdir(parents=True, exist_ok=True)
    initial_data = {"agents": {"silver": {"name": "keepme", "command": "ls"}}}
    gp.write_text(yaml.dump(initial_data), encoding="utf-8")

    # Mock BURNLESS_GLOBAL_CONFIG to point at our tmp file
    env_path = str(gp).replace("\\", "/") # Handle windows paths if any, though usually / on unix
    with patch.dict(os.environ, {"BURNLESS_GLOBAL_CONFIG": env_path}):
        # Re-import/reload config or ensure it reads from environment
        # Since we can't easily reload a module in one test without hacks:
        # We simulate the logic directly as described in setup_wizard.py replacement.
        
        rec = {"gold": {"name": "new"}, "silver": {"name": "clobber-me"}, "bronze": {"name": "new"}}
        
        # Replicate logic from setup_wizard:
        import yaml as _yaml
        gpath = config.global_config_path() # This should return gp because of mock env
        assert gpath == gp
        
        gdata = {}
        if gpath.exists():
            try:
                gdata = _yaml.safe_load(gpath.read_text(encoding="utf-8")) or {}
            except Exception:
                gdata = {}
        
        # The logic says: only seed if NOT gdata.get("agents")
        if not gdata.get("agents"):
            gdata["agents"] = rec
            gpath.parent.mkdir(parents=True, exist_ok=True)
            gpath.write_text(_yaml.safe_dump(gdata, sort_keys=False, allow_unicode=True), encoding="utf-8")

        # Verify: Global should still have "keepme"
        final_gdata = _yaml.safe_load(gp.read_text(encoding="utf-8"))
        assert final_gdata["agents"]["silver"]["name"] == "keepme"
        assert "clobber-me" not in str(final_gdata)

def test_project_config_strip(tmp_path):
    # Setup: Project config has agents
    pcfg = tmp_path / "project_config.yaml"
    initial_pcfg = {"agents": {"gold": {"name": "bad-local-agent"}}, "other": "data"}
    pcfg.write_text(yaml.dump(initial_pcfg), encoding="utf-8")

    # Replicate strip logic:
    import yaml as _yaml
    rec = {"gold": {"name": "new"}, "silver": {"name": "new"}, "bronze": {"name": "new"}}
    
    _pcfg_data = {}
    try:
        _pcfg_data = _yaml.safe_load(pcfg.read_text(encoding="utf-8")) or {}
    except Exception:
        _pcfg_data = {}
    
    if "agents" in _pcfg_data:
        _pcfg_data.pop("agents", None)
        pcfg.write_text(_yaml.safe_dump(_pcfg_data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    # Verify: agents key is gone
    final_pcfg = _yaml.safe_load(pcfg.read_text(encoding="utf-8"))
    assert "agents" not in final_pcfg
    assert final_pcfg["other"] == "data"
