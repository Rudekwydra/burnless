import pytest
from burnless.codec.decoder import normalize_worker_envelope


def test_status_complete_to_ok():
    assert normalize_worker_envelope({"status": "complete"})["status"] == "OK"


def test_status_done_to_ok():
    assert normalize_worker_envelope({"status": "done"})["status"] == "OK"


def test_status_completed_case_insensitive():
    assert normalize_worker_envelope({"status": "Completed"})["status"] == "OK"


def test_status_success_to_ok():
    assert normalize_worker_envelope({"status": "success"})["status"] == "OK"


def test_status_passed_to_ok():
    assert normalize_worker_envelope({"status": "passed"})["status"] == "OK"


def test_status_finished_to_ok():
    assert normalize_worker_envelope({"status": "finished"})["status"] == "OK"


def test_status_partial_to_part():
    assert normalize_worker_envelope({"status": "PARTIAL"})["status"] == "PART"


def test_status_partial_lowercase():
    assert normalize_worker_envelope({"status": "partial"})["status"] == "PART"


def test_status_incomplete_to_part():
    assert normalize_worker_envelope({"status": "incomplete"})["status"] == "PART"


def test_status_failed_to_err():
    assert normalize_worker_envelope({"status": "failed"})["status"] == "ERR"


def test_status_error_to_err():
    assert normalize_worker_envelope({"status": "error"})["status"] == "ERR"


def test_status_fail_to_err():
    assert normalize_worker_envelope({"status": "fail"})["status"] == "ERR"


def test_status_blocked_to_blk():
    assert normalize_worker_envelope({"status": "blocked"})["status"] == "BLK"


def test_status_block_to_blk():
    assert normalize_worker_envelope({"status": "block"})["status"] == "BLK"


def test_status_ok_idempotent():
    assert normalize_worker_envelope({"status": "OK"})["status"] == "OK"


def test_status_part_idempotent():
    assert normalize_worker_envelope({"status": "PART"})["status"] == "PART"


def test_status_err_idempotent():
    assert normalize_worker_envelope({"status": "ERR"})["status"] == "ERR"


def test_status_blk_idempotent():
    assert normalize_worker_envelope({"status": "BLK"})["status"] == "BLK"


def test_no_status_key():
    result = normalize_worker_envelope({})
    assert "status" not in result


def test_no_status_key_with_other_fields():
    result = normalize_worker_envelope({"density": {"efficiency": 0.8}})
    assert "status" not in result


def test_empty_status_value():
    result = normalize_worker_envelope({"status": ""})
    assert result["status"] == ""


def test_whitespace_only_status():
    result = normalize_worker_envelope({"status": "   "})
    assert result["status"] == "   "


def test_status_with_other_fields():
    result = normalize_worker_envelope({
        "status": "complete",
        "salience": 0.8,
        "density": {"efficiency": 0.7}
    })
    assert result["status"] == "OK"
    assert result["salience"] == 0.8
    assert result["density"]["efficiency"] == 0.7
