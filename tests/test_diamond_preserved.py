from burnless.config import _normalize_legacy_tiers


def _make_data(diamond_cmd, silver_cmd):
    return {
        "agents": {
            "diamond": {"name": "fable", "command": diamond_cmd},
            "silver": {"name": "gemma", "command": silver_cmd},
            "gold": {"name": "opus", "command": "x"},
            "bronze": {"name": "haiku", "command": "y"},
        }
    }


def test_diamond_kept_when_silver_is_ollama_empty_command():
    d = _make_data(
        diamond_cmd="claude -p --model fable",
        silver_cmd="",
    )
    d["agents"]["silver"]["provider"] = "ollama-local"
    _normalize_legacy_tiers(d)
    assert d["agents"].get("diamond") is not None, "diamond was wrongly collapsed"
    assert d["agents"].get("silver") is not None


def test_diamond_collapsed_when_same_nonempty_command():
    cmd = "claude -p --model same"
    d = _make_data(diamond_cmd=cmd, silver_cmd=cmd)
    _normalize_legacy_tiers(d)
    assert d["agents"].get("diamond") is None, "diamond should have been collapsed"
    assert d["agents"].get("silver") is not None


def test_diamond_kept_when_different_nonempty_commands():
    d = _make_data(
        diamond_cmd="claude -p --model fable",
        silver_cmd="claude -p --model sonnet",
    )
    _normalize_legacy_tiers(d)
    assert d["agents"].get("diamond") is not None, "diamond was wrongly collapsed"
