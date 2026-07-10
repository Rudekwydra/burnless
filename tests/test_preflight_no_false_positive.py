import tempfile
import os

from burnless.exec.runner import _preflight_verify_block


def test_safe_phrase_no_false_positive_at_rc_zero():
    """rc=0 with 'no syntax errors detected' should NOT be malformed."""
    with tempfile.TemporaryDirectory() as tmp_cwd:
        # Simulate: php -l file.php with no errors (rc=0)
        cmd = "echo 'No syntax errors detected in file.php'"
        complaints = _preflight_verify_block([cmd], cwd=tmp_cwd, timeout=30)
        assert complaints == [], f"Expected no complaints but got: {complaints}"


def test_crash_signature_detected_at_nonzero_rc():
    """rc=1 with 'syntax error' SHOULD be malformed."""
    with tempfile.TemporaryDirectory() as tmp_cwd:
        # Simulate real syntax error: exit 1
        cmd = "sh -c \"echo 'syntax error near line 3'; exit 1\""
        complaints = _preflight_verify_block([cmd], cwd=tmp_cwd, timeout=30)
        assert len(complaints) > 0, "Expected complaints for rc=1 with syntax error signature"
        assert "syntax error" in complaints[0]


def test_crash_signature_ignored_at_rc_zero_without_safe_phrase():
    """rc=0 with crash signature but NO safe-phrase: normally would be ignored."""
    with tempfile.TemporaryDirectory() as tmp_cwd:
        # rc=0 means no malform, even if output has "syntax error"
        cmd = "sh -c \"echo 'some syntax error message'; exit 0\""
        complaints = _preflight_verify_block([cmd], cwd=tmp_cwd, timeout=30)
        # At rc=0, we do NOT flag as malformed based on crash signatures
        assert complaints == []


def test_rc_126_permission_denied_always_malformed():
    """rc=126 (permission denied) is always malformed, independent of output."""
    with tempfile.TemporaryDirectory() as tmp_cwd:
        # rc=126 is a hard error (command not executable)
        # We simulate this by running an unexecutable path
        cmd = "sh -c 'exit 126'"
        complaints = _preflight_verify_block([cmd], cwd=tmp_cwd, timeout=30)
        # rc=126 alone (even with no special signature in output) is malformed
        assert len(complaints) > 0


def test_rc_127_command_not_found_always_malformed():
    """rc=127 (command not found) is always malformed."""
    with tempfile.TemporaryDirectory() as tmp_cwd:
        cmd = "sh -c 'exit 127'"
        complaints = _preflight_verify_block([cmd], cwd=tmp_cwd, timeout=30)
        assert len(complaints) > 0
