import json

from burnless import delegations as deleg_mod
from burnless.codec import decoder as decoder_mod


def test_extract_result_json_backfills_density_and_salience_defaults():
    payload = {
        "id": "d203",
        "status": "OK",
        "kind": "execution",
        "summary": "done",
        "files_touched": [],
        "validated": [],
        "evidence": ["pytest tests"],
        "issues": [],
        "next": "",
    }

    parsed = deleg_mod.extract_result_json(f"```json\n{json.dumps(payload)}\n```")

    assert parsed is not None
    assert parsed["density"] == decoder_mod.DEFAULT_DENSITY
    assert parsed["salience"] == 0.5


def test_extract_result_json_clips_density_and_salience():
    payload = {
        "id": "d203",
        "status": "OK",
        "kind": "execution",
        "summary": "done",
        "files_touched": [],
        "validated": [],
        "evidence": ["pytest tests"],
        "density": {
            "efficiency": -2,
            "creativity": 0.75,
            "out_of_box": 4,
        },
        "salience": 9,
        "issues": [],
        "next": "",
    }

    parsed = deleg_mod.extract_result_json(json.dumps(payload))

    assert parsed is not None
    assert parsed["density"] == {
        "efficiency": 0.0,
        "creativity": 0.75,
        "out_of_box": 1.0,
    }
    assert parsed["salience"] == 1.0


def test_extract_result_json_falls_back_per_density_key():
    payload = {
        "id": "d203",
        "status": "OK",
        "kind": "execution",
        "summary": "done",
        "files_touched": [],
        "validated": [],
        "evidence": ["pytest tests"],
        "density": {
            "efficiency": "bad",
            "creativity": 0.2,
        },
        "salience": "bad",
        "issues": [],
        "next": "",
    }

    parsed = deleg_mod.extract_result_json(json.dumps(payload))

    assert parsed is not None
    assert parsed["density"] == {
        "efficiency": 0.5,
        "creativity": 0.2,
        "out_of_box": 0.5,
    }
    assert parsed["salience"] == 0.5


def test_normalize_files_touched_coerces_dict_entries_to_paths():
    """A worker that reports files_touched as dicts (e.g. {"path": ...}) must
    not crash the syntax gate (isabs/join) or set-based indexing downstream:
    every entry is reduced to a plain path string, unusable entries dropped."""
    payload = {
        "id": "d999",
        "status": "OK",
        "kind": "execution",
        "summary": "done",
        "files_touched": [
            {"path": "src/burnless/recovery.py", "lines": "1-9"},
            "src/burnless/doctor.py",
            {"file": "src/burnless/config.py"},
            {"lines": "no path here"},
            None,
        ],
        "validated": [],
        "evidence": ["pytest tests"],
        "issues": [],
        "next": "",
    }

    normalized = decoder_mod.normalize_worker_envelope(payload)
    files = normalized["files_touched"]

    assert files == [
        "src/burnless/recovery.py",
        "src/burnless/doctor.py",
        "src/burnless/config.py",
    ]
    # every entry is a hashable, joinable string (the properties the crash sites need)
    assert all(isinstance(f, str) for f in files)
    assert len(set(files)) == 3
