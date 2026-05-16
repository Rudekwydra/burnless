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
