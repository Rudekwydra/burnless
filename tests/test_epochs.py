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


def test_needs_consolidation(tmp_path):
    for i in range(9):
        append_epoch(tmp_path, "chatX", f"epoch {i+1}")

    assert needs_consolidation(tmp_path, "chatX", 0) is False

    append_epoch(tmp_path, "chatX", "epoch 10")

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
