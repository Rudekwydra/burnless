import tempfile
from pathlib import Path
from burnless.claude_integration import (
    write_or_update,
    remove_block,
    BLOCK_START,
    BLOCK_END,
    render_block,
)


def test_creates_file_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "CLAUDE.md"
        action = write_or_update(path, "0.7.4", "myproject")
        assert action == "created"
        content = path.read_text()
        assert "# myproject" in content
        assert BLOCK_START in content
        assert BLOCK_END in content


def test_appends_when_existing_without_block():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "CLAUDE.md"
        path.write_text("# Existing project\n\nSome user content here.\n")
        action = write_or_update(path, "0.7.4", "myproject")
        assert action == "appended"
        content = path.read_text()
        assert "Some user content here." in content
        assert BLOCK_START in content
        assert content.index("Some user content here.") < content.index(BLOCK_START)


def test_updates_existing_block_in_place():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "CLAUDE.md"
        path.write_text("# Existing\n\nUser prelude.\n\n")
        write_or_update(path, "0.7.3", "myproject")
        before = path.read_text()
        assert "User prelude." in before
        action = write_or_update(path, "0.7.4", "myproject")
        assert action == "updated"
        after = path.read_text()
        assert "User prelude." in after
        assert "v0.7.4" in after
        assert "v0.7.3" not in after
        assert after.count(BLOCK_START) == 1


def test_preserves_user_content_around_block():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "CLAUDE.md"
        path.write_text(
            "# Project\n\n## My rules\n\n- rule 1\n- rule 2\n\n"
            "<!-- burnless:start v0.7.0 -->\nold burnless content\n<!-- burnless:end -->\n\n"
            "## More rules\n- rule 3\n"
        )
        action = write_or_update(path, "0.7.4", "myproject")
        assert action == "updated"
        content = path.read_text()
        assert "- rule 1" in content
        assert "- rule 2" in content
        assert "- rule 3" in content
        assert "old burnless content" not in content


def test_remove_block_strips_burnless_block():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "CLAUDE.md"
        path.write_text("# Project\n\nUser content\n\n")
        write_or_update(path, "0.7.4", "myproject")
        assert BLOCK_START in path.read_text()
        removed = remove_block(path)
        assert removed is True
        content = path.read_text()
        assert BLOCK_START not in content
        assert "User content" in content


def test_remove_block_noop_when_no_block():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "CLAUDE.md"
        path.write_text("# Project\n\nNo burnless here\n")
        removed = remove_block(path)
        assert removed is False
