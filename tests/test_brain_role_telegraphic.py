from pathlib import Path


def test_brain_role_has_telegraphic_section():
    repo_root = Path(__file__).resolve().parent.parent
    role_path = repo_root / "_design" / "maestro_v1" / "brain_role.md"
    text = role_path.read_text(encoding="utf-8")
    assert "Spec writing for delegates" in text
    assert "telegráf" in text.lower() or "telegraphic" in text.lower()


def test_brain_role_lists_glossary_abbreviations():
    repo_root = Path(__file__).resolve().parent.parent
    role_path = repo_root / "_design" / "maestro_v1" / "brain_role.md"
    text = role_path.read_text(encoding="utf-8")
    for abbrev in ("imp=implementar", "val=validar", "cfg=configuração"):
        assert abbrev in text, f"missing abbreviation: {abbrev}"


def test_brain_role_has_good_and_bad_examples():
    repo_root = Path(__file__).resolve().parent.parent
    role_path = repo_root / "_design" / "maestro_v1" / "brain_role.md"
    text = role_path.read_text(encoding="utf-8")
    assert "BOM" in text or "GOOD" in text or "Exemplo" in text
    assert "RUIM" in text or "BAD" in text or "verbose" in text.lower()
