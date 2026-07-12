import pytest
from burnless.cli import _extract_rollover_meta


def test_extract_rollover_meta_with_prepared_data():
    """Test _extract_rollover_meta with realistic monitor rollover data shape."""
    rollover_result = {
        "checks": 3,
        "last": {
            "status": "prepared",
            "prepared": {
                "restore": {
                    "recovery": {
                        "checkpoint_chars": 123,
                        "pending_count": 2,
                        "checkpoint_generation": "gen-1",
                        "journal_head": "jh-1",
                        "applied_through": "applied-1",
                        "watermark_gap": 10,
                        "truncated": False,
                    },
                    "hookSpecificOutput": {
                        "additionalContext": "SEED",
                    }
                },
                "run_state": {
                    "count": 7,
                }
            },
            "new_session_id": "abc-fresh",
        }
    }

    last, prepared, new_session_id = _extract_rollover_meta(rollover_result)

    assert last.get("status") == "prepared"
    assert new_session_id == "abc-fresh"
    assert prepared.get("restore", {}).get("recovery", {}).get("checkpoint_chars") == 123
    assert prepared.get("restore", {}).get("recovery", {}).get("pending_count") == 2
    assert prepared.get("run_state", {}).get("count") == 7
    assert prepared.get("restore", {}).get("hookSpecificOutput", {}).get("additionalContext") == "SEED"


def test_extract_rollover_meta_with_empty_dict():
    """Test _extract_rollover_meta with empty dict."""
    last, prepared, new_session_id = _extract_rollover_meta({})

    assert last == {}
    assert prepared == {}
    assert new_session_id is None


def test_extract_rollover_meta_with_none():
    """Test _extract_rollover_meta with None."""
    last, prepared, new_session_id = _extract_rollover_meta(None)

    assert last == {}
    assert prepared == {}
    assert new_session_id is None


def test_extract_rollover_meta_with_missing_last():
    """Test _extract_rollover_meta when 'last' key is missing."""
    rollover_result = {"checks": 3}

    last, prepared, new_session_id = _extract_rollover_meta(rollover_result)

    assert last == {}
    assert prepared == {}
    assert new_session_id is None


def test_extract_rollover_meta_with_missing_prepared():
    """Test _extract_rollover_meta when 'prepared' key is missing from last."""
    rollover_result = {
        "checks": 3,
        "last": {
            "status": "prepared",
            "new_session_id": "xyz-fresh",
        }
    }

    last, prepared, new_session_id = _extract_rollover_meta(rollover_result)

    assert last.get("status") == "prepared"
    assert prepared == {}
    assert new_session_id == "xyz-fresh"
