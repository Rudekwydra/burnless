from __future__ import annotations

import json

from burnless import state


def test_set_next_populates_all_fields():
    st = dict(state.DEFAULT_STATE)
    state.set_next(st, "faz X", plan_id="d100", revision=1)
    assert st["next"] == "faz X"
    assert st["next_plan_id"] == "d100"
    assert st["next_revision"] == 1
    assert isinstance(st["next_updated_at"], str) and st["next_updated_at"]
    assert st["next_source"] == "worker"


def test_set_next_new_plan_supersedes_old():
    st = dict(state.DEFAULT_STATE)
    state.set_next(st, "faz X", plan_id="d100", revision=1)
    state.set_next(st, "faz Y", plan_id="d101", revision=1)
    assert st["next"] == "faz Y"
    assert st["next_plan_id"] == "d101"


def test_set_next_ignores_out_of_order_same_plan():
    st = dict(state.DEFAULT_STATE)
    state.set_next(st, "faz X rev2", plan_id="d100", revision=2)
    state.set_next(st, "faz X rev1 tardio", plan_id="d100", revision=1)
    assert st["next"] == "faz X rev2"


def test_set_next_higher_revision_same_plan_applies():
    st = dict(state.DEFAULT_STATE)
    state.set_next(st, "v1", plan_id="d100", revision=1)
    state.set_next(st, "v2", plan_id="d100", revision=2)
    assert st["next"] == "v2"
    assert st["next_revision"] == 2
    assert "v1" not in st.values()


def test_invalidate_next_clears_matching_plan():
    st = dict(state.DEFAULT_STATE)
    state.set_next(st, "faz X", plan_id="d100", revision=1)
    state.invalidate_next(st, plan_id="d100")
    assert st["next"] is None
    assert st["next_plan_id"] is None


def test_invalidate_next_ignores_stale_plan_id():
    st = dict(state.DEFAULT_STATE)
    state.set_next(st, "faz X", plan_id="d100", revision=1)
    state.set_next(st, "faz Y", plan_id="d101", revision=1)
    state.invalidate_next(st, plan_id="d100")
    assert st["next"] == "faz Y"


def test_invalidate_next_no_plan_id_always_clears():
    st = dict(state.DEFAULT_STATE)
    state.set_next(st, "faz X", plan_id="d100", revision=1)
    state.invalidate_next(st)
    assert st["next"] is None


def test_default_state_has_new_keys():
    assert state.DEFAULT_STATE["next_plan_id"] is None
    assert state.DEFAULT_STATE["next_revision"] is None
    assert state.DEFAULT_STATE["next_updated_at"] is None
    assert state.DEFAULT_STATE["next_source"] is None


def test_load_backfills_new_keys_for_legacy_state_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"project": "x", "next": "old text"}), encoding="utf-8")
    data = state.load(path)
    assert data["next_plan_id"] is None
    assert data["next_revision"] is None
    assert data["next_updated_at"] is None
    assert data["next_source"] is None
    assert data["next"] == "old text"
