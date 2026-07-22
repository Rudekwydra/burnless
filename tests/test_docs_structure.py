from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_doctrine_mentions_codex_parity():
    content = (ROOT / "docs" / "DOCTRINE.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in content
    assert "setup --codex" in content


def test_skill_file_exists_and_has_frontmatter():
    path = ROOT / "templates" / "codex" / "skills" / "burnless-router" / "SKILL.md"
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---")
    assert "name: burnless-router" in content
    for term in ("bronze", "silver", "gold", "diamond"):
        assert term in content


def test_burnless_for_llms_mentions_codex():
    content = (ROOT / "BURNLESS_FOR_LLMS.md").read_text(encoding="utf-8")
    assert "Codex" in content
