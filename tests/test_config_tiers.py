from pathlib import Path

from burnless import config


def test_legacy_diamond_codex_becomes_silver(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
agents:
  diamond:
    name: codex
    command: codex exec --sandbox workspace-write
routing:
  diamond:
    - bug
    - test
""",
        encoding="utf-8",
    )

    cfg = config.load(cfg_path)

    assert "diamond" not in cfg["agents"]
    assert cfg["agents"]["silver"]["name"] == "codex"
    assert "bug" in cfg["routing"]["silver"]
    assert "test" in cfg["routing"]["silver"]
