import pytest
from pathlib import Path
from burnless.epochs import (
    append_epoch,
    consolidate_level,
    active_chain,
    needs_consolidation,
    cleanup_originais,
    epoch_dir,
    _slot_name,
)


def test_slot_name():
    assert _slot_name(0, 1) == "001.md"
    assert _slot_name(0, 10) == "010.md"
    assert _slot_name(1, 1) == "a01.md"
    assert _slot_name(1, 10) == "a10.md"
    assert _slot_name(2, 3) == "b03.md"


def test_append_and_slot_names(tmp_path):
    for i in range(3):
        append_epoch(tmp_path, "chatX", f"epoch {i+1}")

    d = epoch_dir(tmp_path, "chatX")
    assert (d / "001.md").exists()
    assert (d / "002.md").exists()
    assert (d / "003.md").exists()

    assert (d / "001.md").read_text() == "epoch 1"
    assert (d / "002.md").read_text() == "epoch 2"
    assert (d / "003.md").read_text() == "epoch 3"


def test_append_epoch_uses_max_plus_one_after_gap(tmp_path):
    d = epoch_dir(tmp_path, "chatGap")
    d.mkdir(parents=True, exist_ok=True)
    (d / "001.md").write_text("first", encoding="utf-8")
    (d / "003.md").write_text("third", encoding="utf-8")

    new_path = append_epoch(tmp_path, "chatGap", "fourth")
    assert new_path.name == "004.md"


def test_needs_consolidation(tmp_path):
    for i in range(9):
        append_epoch(tmp_path, "chatX", f"epoch {i+1}")

    assert needs_consolidation(tmp_path, "chatX", 0) is False

    append_epoch(tmp_path, "chatX", "epoch 10")

    assert needs_consolidation(tmp_path, "chatX", 0) is True


def test_needs_consolidation_triggers_on_more_than_ten(tmp_path):
    for i in range(11):
        append_epoch(tmp_path, "chatX", f"epoch {i+1}")

    assert needs_consolidation(tmp_path, "chatX", 0) is True


def test_consolidate_moves_to_originais(tmp_path):
    up = lambda text: "CONS:" + str(len(text))

    for i in range(10):
        append_epoch(tmp_path, "chatX", f"epoch {i+1}")

    d = epoch_dir(tmp_path, "chatX")

    result = consolidate_level(tmp_path, "chatX", 0, up)

    assert result is not None
    assert result.name == "a01.md"
    assert (d / "a01.md").exists()

    assert not (d / "001.md").exists()
    assert not (d / "010.md").exists()

    assert (d / "originais" / "001.md").exists()
    assert (d / "originais" / "010.md").exists()


def test_consolidate_uses_max_plus_one_after_gap(tmp_path):
    up = lambda text: "CONS:" + str(len(text))

    d = epoch_dir(tmp_path, "chatX")
    d.mkdir(parents=True, exist_ok=True)
    (d / "001.md").write_text("epoch 1", encoding="utf-8")
    (d / "003.md").write_text("epoch 3", encoding="utf-8")
    for i in range(2, 11):
        if i == 3:
            continue
        (d / f"{i:03d}.md").write_text(f"epoch {i}", encoding="utf-8")

    result = consolidate_level(tmp_path, "chatX", 0, up)
    assert result is not None
    assert result.name == "a01.md"

    (d / "b01.md").write_text("older level 1", encoding="utf-8")
    (d / "b03.md").write_text("older level 3", encoding="utf-8")
    for i in range(1, 11):
        (d / f"a{i:02d}.md").write_text(f"level1 epoch {i}", encoding="utf-8")
    result2 = consolidate_level(tmp_path, "chatX", 1, up)
    assert result2 is not None
    assert result2.name == "b04.md"


def test_consolidate_fail_open(tmp_path):
    fail_summarizer = lambda text: None

    for i in range(10):
        append_epoch(tmp_path, "chatX", f"epoch {i+1}")

    d = epoch_dir(tmp_path, "chatX")

    result = consolidate_level(tmp_path, "chatX", 0, fail_summarizer)

    assert result is None

    assert (d / "001.md").exists()
    assert (d / "010.md").exists()

    assert not (d / "a01.md").exists()
    assert not (d / "originais").exists()


def test_active_chain(tmp_path):
    up = lambda text: "CONS:" + str(len(text))

    for i in range(10):
        append_epoch(tmp_path, "chatX", f"epoch {i+1}")

    consolidate_level(tmp_path, "chatX", 0, up)

    append_epoch(tmp_path, "chatX", "epoch 11")
    append_epoch(tmp_path, "chatX", "epoch 12")

    chain = active_chain(tmp_path, "chatX")
    names = [p.name for p in chain]

    assert names == ["a01.md", "001.md", "002.md"]

    assert not any("originais" in str(f) for f in chain)


def test_cleanup_originais(tmp_path):
    up = lambda text: "CONS:" + str(len(text))

    for i in range(10):
        append_epoch(tmp_path, "chatX", f"epoch {i+1}")

    consolidate_level(tmp_path, "chatX", 0, up)

    d = epoch_dir(tmp_path, "chatX")
    assert (d / "originais").exists()

    count = cleanup_originais(tmp_path, "chatX")

    assert count >= 10
    assert not (d / "originais").exists()


def test_carry_forward_merges_recent_chains(tmp_path):
    """A deep working chain must not be orphaned by a thinner, newer session.

    Regression: carry_forward_chain used to return only the single newest chat
    dir (by dir mtime), so a 1-epoch throwaway session shadowed a multi-epoch
    working chain. It must merge recent chains, newest-first.
    """
    import os
    from burnless.epochs import carry_forward_chain

    for i in range(3):
        append_epoch(tmp_path, "chatDeep", f"deep epoch {i+1}")
    append_epoch(tmp_path, "chatThin", "thin epoch 1")

    # Make the thin chain the most recently active.
    for f in epoch_dir(tmp_path, "chatDeep").glob("*.md"):
        os.utime(f, (1000, 1000))
    for f in epoch_dir(tmp_path, "chatThin").glob("*.md"):
        os.utime(f, (2000, 2000))

    out = carry_forward_chain(tmp_path, current_chat_id="chatCurrent")

    # Both chains survive — the deep chain is NOT orphaned.
    assert "thin epoch 1" in out
    assert "deep epoch 1" in out
    assert "deep epoch 3" in out
    # Newest-first: the fresher thin chain leads the merge.
    assert out.index("thin epoch 1") < out.index("deep epoch 3")
    assert "mais NOVO primeiro" in out


def test_carry_forward_excludes_current_chat(tmp_path):
    """The current chat's own epochs are never replayed back to it."""
    from burnless.epochs import carry_forward_chain

    append_epoch(tmp_path, "chatCurrent", "my own epoch")
    append_epoch(tmp_path, "chatOther", "other epoch")

    out = carry_forward_chain(tmp_path, current_chat_id="chatCurrent")

    assert "other epoch" in out
    assert "my own epoch" not in out


def test_carry_forward_dedups_repeated_summaries(tmp_path):
    """Identical epoch bodies across chains collapse to one (kills seed echoes)."""
    from burnless.epochs import carry_forward_chain

    append_epoch(tmp_path, "chatA", "duplicated body")
    append_epoch(tmp_path, "chatB", "duplicated body")

    out = carry_forward_chain(tmp_path, current_chat_id="chatCurrent")

    assert out.count("duplicated body") == 1
