"""Tests for reconcile_worker_status() — PART→OK override when spec ## Verify passes."""

import pytest
from burnless.exec import runner


class TestReconcileWorkerStatus:
    """Test reconcile_worker_status() deterministic promotion logic."""

    def test_part_with_full_verify_marker_promotes_to_ok(self):
        """Case (a): PART + "verify: 3/3 checks passed" → OK with runner-override."""
        summary = {
            "id": "d042",
            "status": "PART",
            "kind": "execution",
            "summary": "worker self-reported PART",
            "files_touched": ["src/file.py"],
            "validated": ["verify: 3/3 checks passed"],
            "issues": ["check_failed", "config_error"],
            "next": "fix the issue",
        }

        result = runner.reconcile_worker_status(summary)

        assert result["status"] == "OK", "Status should be promoted to OK"
        assert len(result["validated"]) == 2, "Should have original marker + runner-override"
        assert any(
            "runner-override" in str(v) for v in result["validated"]
        ), "runner-override marker should be added"
        assert any(
            "3/3" in str(v) for v in result["validated"]
        ), "runner-override should reference 3/3"
        assert len(result["issues"]) == 2, "Should preserve issues count"
        assert all(
            str(i).startswith("worker_selfcheck:") for i in result["issues"]
        ), "All issues should be prefixed with worker_selfcheck:"

    def test_part_with_partial_verify_marker_unchanged(self):
        """Case (b): PART + "verify: 2/3 checks passed" → PART unchanged."""
        summary = {
            "id": "d042",
            "status": "PART",
            "kind": "execution",
            "summary": "worker self-reported PART",
            "files_touched": ["src/file.py"],
            "validated": ["verify: 2/3 checks passed"],
            "issues": ["verify_failed"],
            "next": "rerun verify",
        }

        result = runner.reconcile_worker_status(summary)

        assert result["status"] == "PART", "Status should remain PART (not all checks passed)"
        assert result == summary, "Summary should be returned unchanged"

    def test_part_without_marker_unchanged(self):
        """Case (c): PART without verify marker → PART unchanged."""
        summary = {
            "id": "d042",
            "status": "PART",
            "kind": "execution",
            "summary": "worker self-reported PART",
            "files_touched": ["src/file.py"],
            "validated": [],
            "issues": ["syntax_failed"],
            "next": "fix syntax",
        }

        result = runner.reconcile_worker_status(summary)

        assert result["status"] == "PART", "Status should remain PART"
        assert result == summary, "Summary should be returned unchanged"

    def test_ok_with_marker_unchanged_no_duplication(self):
        """Case (d): OK + marker → OK unchanged (no duplication)."""
        summary = {
            "id": "d042",
            "status": "OK",
            "kind": "execution",
            "summary": "work completed",
            "files_touched": ["src/file.py"],
            "validated": ["verify: 3/3 checks passed"],
            "issues": [],
            "next": "",
        }

        result = runner.reconcile_worker_status(summary)

        assert result["status"] == "OK", "Status should remain OK"
        assert result == summary, "Summary should be returned unchanged"

    def test_err_status_unchanged(self):
        """ERR status should pass through unchanged."""
        summary = {
            "id": "d042",
            "status": "ERR",
            "kind": "execution",
            "summary": "error occurred",
            "files_touched": [],
            "validated": ["verify: 3/3 checks passed"],
            "issues": ["timeout"],
            "next": "retry",
        }

        result = runner.reconcile_worker_status(summary)

        assert result["status"] == "ERR", "ERR status should not be promoted"
        assert result == summary, "Summary should be returned unchanged"

    def test_part_zero_checks_passed_unchanged(self):
        """PART with "verify: 0/0 checks passed" should not promote (N must be > 0)."""
        summary = {
            "id": "d042",
            "status": "PART",
            "kind": "execution",
            "summary": "failed",
            "files_touched": [],
            "validated": ["verify: 0/0 checks passed"],
            "issues": ["no_checks"],
            "next": "add tests",
        }

        result = runner.reconcile_worker_status(summary)

        assert result["status"] == "PART", "Status should remain PART (0/0 is not a success)"
        assert result == summary, "Summary should be returned unchanged"

    def test_part_with_multiple_markers_uses_first_full_match(self):
        """If validated has multiple markers, use the first N/N match."""
        summary = {
            "id": "d042",
            "status": "PART",
            "kind": "execution",
            "summary": "partial verification",
            "files_touched": ["src/file.py"],
            "validated": [
                "some_other_validation",
                "verify: 5/5 checks passed",
                "verify: 2/2 checks passed",  # multiple markers; should use first
            ],
            "issues": ["worker_issue"],
            "next": "",
        }

        result = runner.reconcile_worker_status(summary)

        assert result["status"] == "OK", "Should promote to OK using first full marker"
        assert any(
            "5/5" in str(v) for v in result["validated"]
        ), "runner-override should reference 5/5 (first marker)"

    def test_issues_already_prefixed_not_double_prefixed(self):
        """If issues already start with worker_selfcheck:, should not double-prefix."""
        summary = {
            "id": "d042",
            "status": "PART",
            "kind": "execution",
            "summary": "failed",
            "files_touched": [],
            "validated": ["verify: 2/2 checks passed"],
            "issues": ["worker_selfcheck: already_prefixed", "new_issue"],
            "next": "",
        }

        result = runner.reconcile_worker_status(summary)

        assert result["status"] == "OK", "Should promote to OK"
        assert len(result["issues"]) == 2
        assert result["issues"][0] == "worker_selfcheck: already_prefixed", "Should not double-prefix"
        assert result["issues"][1] == "worker_selfcheck: new_issue", "Should prefix new issue"

    def test_empty_validated_list(self):
        """PART with empty validated list should remain unchanged."""
        summary = {
            "id": "d042",
            "status": "PART",
            "kind": "execution",
            "summary": "failed",
            "files_touched": [],
            "validated": [],
            "issues": ["some_issue"],
            "next": "",
        }

        result = runner.reconcile_worker_status(summary)

        assert result["status"] == "PART"
        assert result == summary

    def test_none_validated_handled_gracefully(self):
        """PART with None validated should not crash."""
        summary = {
            "id": "d042",
            "status": "PART",
            "kind": "execution",
            "summary": "failed",
            "files_touched": [],
            "validated": None,  # None instead of list
            "issues": ["some_issue"],
            "next": "",
        }

        result = runner.reconcile_worker_status(summary)

        assert result["status"] == "PART", "Should remain PART"
        # Function should handle None gracefully

    def test_preserved_all_other_fields(self):
        """All other fields (kind, summary, files_touched, next) should be preserved."""
        summary = {
            "id": "d042",
            "status": "PART",
            "kind": "thought",
            "summary": "original summary text",
            "files_touched": ["file1.py", "file2.py"],
            "validated": ["verify: 2/2 checks passed"],
            "issues": ["issue1"],
            "next": "next step",
            "extra_field": "should_be_preserved",
        }

        result = runner.reconcile_worker_status(summary)

        assert result["status"] == "OK"
        assert result["kind"] == "thought", "kind should be preserved"
        assert result["summary"] == "original summary text", "summary should be preserved"
        assert result["files_touched"] == ["file1.py", "file2.py"], "files_touched should be preserved"
        assert result["next"] == "next step", "next should be preserved"
        assert result.get("extra_field") == "should_be_preserved", "extra fields should be preserved"


class TestReconcileRealFlow:
    """E2E of the d042 arbitration: the runner gates now RUN on worker-claimed
    PART (they were no-op before, making reconcile dead code in the real flow)."""

    def test_verify_gate_runs_on_part_then_reconcile_promotes(self, tmp_path):
        log_path = tmp_path / "d.log"
        log_path.write_text("", encoding="utf-8")
        summary = {"status": "PART", "validated": [], "issues": ["invented check failed"], "next": ""}
        gated = runner._apply_verify_gate(
            summary, ["true", "true"], cwd=tmp_path, did="dX", log_path=log_path, timeout=10
        )
        assert gated["status"] == "PART", "gate never promotes by itself"
        assert any("verify: 2/2 checks passed" in str(v) for v in gated["validated"])
        final = runner.reconcile_worker_status(gated)
        assert final["status"] == "OK"
        assert any("runner-override" in str(v) for v in final["validated"])

    def test_verify_gate_failure_on_part_blocks_promotion(self, tmp_path):
        log_path = tmp_path / "d.log"
        log_path.write_text("", encoding="utf-8")
        summary = {"status": "PART", "validated": [], "issues": [], "next": ""}
        gated = runner._apply_verify_gate(
            summary, ["false"], cwd=tmp_path, did="dX", log_path=log_path, timeout=10
        )
        assert gated["status"] == "PART"
        assert any("verify_failed" in str(i) for i in gated["issues"])
        final = runner.reconcile_worker_status(gated)
        assert final["status"] == "PART", "runner evidence must never be overridden"

    def test_reconcile_never_promotes_over_syntax_failed(self):
        summary = {
            "status": "PART",
            "validated": ["verify: 3/3 checks passed"],
            "issues": ["syntax_failed: py_compile x.py (rc=1): boom"],
            "next": "",
        }
        final = runner.reconcile_worker_status(summary)
        assert final["status"] == "PART"
