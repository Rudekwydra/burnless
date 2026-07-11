"""Tests for P10/2: race-safe update_locked prevents lost updates in parallel dispatch."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from burnless import state as state_mod


def test_update_locked_preserves_foreign_writes(tmp_path: Path):
    """Demonstrate that update_locked preserves concurrent foreign writes,
    reading the fresh disk state before applying mutations."""
    state_path = tmp_path / "state.json"

    # Initialize state file
    state_mod.save(state_path, {"turn_counter": 0})

    # Simulate process A: load snapshot (captures old state)
    state_a = state_mod.load(state_path)

    # Simulate process B: update_locked increments turn_counter and sets foreign_key
    def b_mutator(st: dict) -> None:
        st["turn_counter"] = int(st.get("turn_counter", 0) or 0) + 1
        st["foreign_key"] = "B"
    state_mod.update_locked(state_path, b_mutator)

    # Verify B's changes are on disk
    disk_state = state_mod.load(state_path)
    assert disk_state["turn_counter"] == 1
    assert disk_state["foreign_key"] == "B"

    # Process A: now uses update_locked (the new pattern) to persist its changes
    # The mutator reads the CURRENT disk state (with B's counter=1 and foreign_key=B)
    # and increments only the turn_counter
    def a_mutator(st: dict) -> None:
        st["turn_counter"] = int(st.get("turn_counter", 0) or 0) + 1

    state_a_fresh = state_mod.update_locked(state_path, a_mutator)

    # Verify BOTH B's foreign_key and A's increment are preserved
    disk_state = state_mod.load(state_path)
    assert disk_state["turn_counter"] == 2, "A's mutator increments disk's 1 to 2"
    assert disk_state["foreign_key"] == "B", "B's foreign_key is preserved because mutator reads fresh disk"


def test_save_locked_loses_increments(tmp_path: Path):
    """Demonstrate the old pattern (save_locked with stale snapshot) LOSES concurrent increments.
    This documents why update_locked with mutator is mandatory."""
    state_path = tmp_path / "state.json"

    # Initialize state file
    state_mod.save(state_path, {"turn_counter": 0})

    # Simulate process A: load snapshot (captures old state with turn_counter=0)
    state_a = state_mod.load(state_path)

    # Simulate process B: update_locked increments turn_counter to 1
    def b_mutator(st: dict) -> None:
        st["turn_counter"] = int(st.get("turn_counter", 0) or 0) + 1
    state_mod.update_locked(state_path, b_mutator)

    # Verify B's increment is on disk
    disk_state = state_mod.load(state_path)
    assert disk_state["turn_counter"] == 1

    # Process A: uses the OLD pattern (save_locked with stale snapshot)
    # A's snapshot has turn_counter=0, so when A increments, it becomes 1
    state_a["turn_counter"] = int(state_a.get("turn_counter", 0) or 0) + 1
    state_mod.save_locked(state_path, state_a)

    # After save_locked with stale snapshot, B's increment is LOST
    # A's snapshot was old (0), so A incremented to 1, overwriting B's increment to 1
    disk_state = state_mod.load(state_path)
    assert disk_state["turn_counter"] == 1, (
        "Expected turn_counter=1 (lost update): A loaded 0, incremented to 1, "
        "overwriting B's 1 because save_locked merged A's stale snapshot"
    )


def test_concurrent_update_locked_no_lost_increment(tmp_path: Path):
    """8 threads concurrently increment turn_counter via update_locked.
    Final value must be 8 (no lost updates)."""
    state_path = tmp_path / "state.json"
    state_mod.save(state_path, {"turn_counter": 0})

    errors = []

    def increment():
        try:
            def mutator(st: dict) -> None:
                st["turn_counter"] = int(st.get("turn_counter", 0) or 0) + 1
            state_mod.update_locked(state_path, mutator)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=increment) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"

    # All 8 increments must be visible on disk
    final_state = state_mod.load(state_path)
    assert final_state["turn_counter"] == 8, (
        f"Expected turn_counter=8, got {final_state['turn_counter']} "
        "(lost updates detected)"
    )
