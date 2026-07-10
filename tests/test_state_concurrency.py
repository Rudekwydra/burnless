import json
import threading
from pathlib import Path

import pytest

from burnless import state as state_mod


def test_save_locked_concurrent_writers(tmp_path):
    """Test that multiple threads writing to the same state.json via save_locked
    produce consistent, non-corrupted results with all writes preserved.
    """
    state_path = tmp_path / "state.json"
    exceptions = []

    def writer(thread_id):
        try:
            st = state_mod.load(state_path)
            st[f"writer_{thread_id}"] = True
            state_mod.save_locked(state_path, st)
        except Exception as e:
            exceptions.append((thread_id, e))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not exceptions, f"Exceptions in concurrent writes: {exceptions}"

    final_state = state_mod.load(state_path)
    for i in range(8):
        assert f"writer_{i}" in final_state, f"Missing writer_{i} in final state"
        assert final_state[f"writer_{i}"] is True


def test_save_error_handling(tmp_path, monkeypatch):
    """Test that json.dump failures are wrapped in RuntimeError with context."""
    state_path = tmp_path / "state.json"
    st = {"key": "value"}

    call_count = [0]
    original_dump = json.dump

    def failing_dump(obj, fp, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise OSError("Simulated disk write failure")
        return original_dump(obj, fp, **kwargs)

    monkeypatch.setattr("json.dump", failing_dump)

    with pytest.raises(RuntimeError) as exc_info:
        state_mod.save(state_path, st)

    assert "state save failed" in str(exc_info.value)


def test_save_locked_error_handling(tmp_path, monkeypatch):
    """Test that save_locked() properly handles errors from save()."""
    state_path = tmp_path / "state.json"
    st = {"key": "value"}

    call_count = [0]
    original_dump = json.dump

    def failing_dump(obj, fp, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise OSError("Simulated disk write failure")
        return original_dump(obj, fp, **kwargs)

    monkeypatch.setattr("json.dump", failing_dump)

    with pytest.raises(RuntimeError) as exc_info:
        state_mod.save_locked(state_path, st)

    assert "state save failed" in str(exc_info.value)


def test_save_locked_merges_existing_state(tmp_path):
    """Test that save_locked() loads and merges with existing state."""
    state_path = tmp_path / "state.json"

    state_mod.save(state_path, {"existing_key": "existing_value", "counter": 1})

    state_mod.save_locked(state_path, {"counter": 2, "new_key": "new_value"})

    final = state_mod.load(state_path)
    assert final["existing_key"] == "existing_value"
    assert final["counter"] == 2
    assert final["new_key"] == "new_value"
