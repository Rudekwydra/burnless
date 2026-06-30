import pytest
from burnless.epochs_v2 import parse_living_v3, _rebuild_md_v3


def test_living_v3_codeblock_roundtrip():
    """Code block in a section should roundtrip intact without re-bulleting."""
    doc = """## Refs
- some ref
```
foo bar
baz
```
- another ref
"""
    parsed = parse_living_v3(doc)
    rebuilt = _rebuild_md_v3(parsed)

    assert '```\nfoo bar\nbaz\n```' in rebuilt
    assert '- some ref' in rebuilt
    assert '- another ref' in rebuilt

    parsed_again = parse_living_v3(rebuilt)
    rebuilt_again = _rebuild_md_v3(parsed_again)

    assert rebuilt == rebuilt_again

    entries = parsed['Refs']
    orphan_fences = [e for e in entries if e.strip() == '```']
    empty_entries = [e for e in entries if not e.strip()]
    assert len(orphan_fences) == 0, "should not have orphan fence entries"
    assert len(empty_entries) == 0, "should not have empty entries"


def test_living_v3_no_empty_entries():
    """Blank lines between bullets should not create empty entries."""
    doc = """## Threads abertas
- thread 1

- thread 2


- thread 3
"""
    parsed = parse_living_v3(doc)
    entries = parsed['Threads abertas']

    empty_entries = [e for e in entries if not e.strip()]
    assert len(empty_entries) == 0, "should not produce empty entries from blank lines"
    assert len(entries) == 3
    assert 'thread 1' in entries[0]
    assert 'thread 2' in entries[1]
    assert 'thread 3' in entries[2]
