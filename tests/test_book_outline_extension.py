import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_BOOK_SCRIPTS = Path(__file__).resolve().parents[1] / "book" / "scripts"
pytestmark = pytest.mark.skipif(not _BOOK_SCRIPTS.exists(), reason="book/ not present in this checkout")
sys.path.insert(0, str(_BOOK_SCRIPTS))

_BOOK_DIR = Path(__file__).resolve().parents[1] / "book"
_CHAPTER_PLAN_PATH = str(_BOOK_DIR / "chapter_plan.json")
_EXTEND_OUTLINE_SCRIPT = str(_BOOK_DIR / "scripts" / "extend_outline.py")


def test_extend_outline_dry_run_no_modify():
    """Verify dry-run doesn't modify the plan file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy real plan to tmp
        real_plan = _CHAPTER_PLAN_PATH
        tmp_plan = os.path.join(tmpdir, "chapter_plan.json")
        with open(real_plan) as src:
            with open(tmp_plan, "w") as dst:
                dst.write(src.read())

        # Get original bytes
        with open(tmp_plan, "rb") as f:
            original_bytes = f.read()

        # Run dry-run
        result = subprocess.run(
            [
                "python3",
                _EXTEND_OUTLINE_SCRIPT,
                "--plan", tmp_plan,
                "--dry-run",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"dry-run failed: {result.stderr}"

        # Parse output JSON
        output = json.loads(result.stdout.strip())
        assert "new_capsules" in output
        assert "next_chapter_num" in output
        assert "over_threshold" in output

        # Verify plan unchanged
        with open(tmp_plan, "rb") as f:
            after_bytes = f.read()
        assert original_bytes == after_bytes, "dry-run modified plan file"


def test_extend_outline_dedup_existing_capsules():
    """Verify capsules already in plan don't count as new."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create minimal frozen dir with 3 capsules: 1 old, 2 new
        frozen_dir = os.path.join(tmpdir, "frozen")
        os.makedirs(frozen_dir)

        # Capsule already in chapter 1
        old_cap = {
            "name": "antigravity-claude-md-soul",
            "summary": "Already in chapter 1",
            "created": "2026-05-10T00:00:00Z",
            "updated": None,
        }
        with open(os.path.join(frozen_dir, "old_cap.json"), "w") as f:
            json.dump(old_cap, f)

        # Two new capsules after cutoff
        for i, name in enumerate(["new-cap-1", "new-cap-2"]):
            new_cap = {
                "name": name,
                "summary": f"New capsule {i+1}",
                "created": f"2026-05-25T{i:02d}:00:00Z",
                "updated": None,
            }
            with open(os.path.join(frozen_dir, f"{name}.json"), "w") as f:
                json.dump(new_cap, f)

        # Copy real plan
        real_plan = _CHAPTER_PLAN_PATH
        tmp_plan = os.path.join(tmpdir, "chapter_plan.json")
        with open(real_plan) as src:
            with open(tmp_plan, "w") as dst:
                dst.write(src.read())

        # Run dry-run against minimal frozen dir
        result = subprocess.run(
            [
                "python3",
                _EXTEND_OUTLINE_SCRIPT,
                "--plan", tmp_plan,
                "--frozen-dir", frozen_dir,
                "--dry-run",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"dry-run failed: {result.stderr}"
        output = json.loads(result.stdout.strip())

        # Should count only 2 new (not the already-used one)
        assert output["new_capsules"] == 2, f"Expected 2 new, got {output['new_capsules']}"


def test_extend_outline_json_structure():
    """Verify dry-run output has required fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy real plan
        real_plan = _CHAPTER_PLAN_PATH
        tmp_plan = os.path.join(tmpdir, "chapter_plan.json")
        with open(real_plan) as src:
            with open(tmp_plan, "w") as dst:
                dst.write(src.read())

        result = subprocess.run(
            [
                "python3",
                _EXTEND_OUTLINE_SCRIPT,
                "--plan", tmp_plan,
                "--dry-run",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = json.loads(result.stdout.strip())

        required_keys = ["new_capsules", "next_chapter_num", "over_threshold"]
        for key in required_keys:
            assert key in output, f"Missing required key: {key}"

        assert isinstance(output["new_capsules"], int)
        assert isinstance(output["next_chapter_num"], int)
        assert isinstance(output["over_threshold"], bool)
        assert output["next_chapter_num"] > 12  # After existing 12 chapters


def test_extend_outline_no_apply_zero_capsules():
    """Verify --apply with zero new capsules outputs applied=0 and exits cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Empty frozen dir (no capsules)
        frozen_dir = os.path.join(tmpdir, "frozen")
        os.makedirs(frozen_dir)

        # Copy real plan
        real_plan = _CHAPTER_PLAN_PATH
        tmp_plan = os.path.join(tmpdir, "chapter_plan.json")
        with open(real_plan) as src:
            with open(tmp_plan, "w") as dst:
                dst.write(src.read())

        result = subprocess.run(
            [
                "python3",
                _EXTEND_OUTLINE_SCRIPT,
                "--plan", tmp_plan,
                "--frozen-dir", frozen_dir,
                "--apply",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert output["applied"] == 0


if __name__ == "__main__":
    # Run all test functions
    test_extend_outline_dry_run_no_modify()
    print("✓ test_extend_outline_dry_run_no_modify")

    test_extend_outline_dedup_existing_capsules()
    print("✓ test_extend_outline_dedup_existing_capsules")

    test_extend_outline_json_structure()
    print("✓ test_extend_outline_json_structure")

    test_extend_outline_no_apply_zero_capsules()
    print("✓ test_extend_outline_no_apply_zero_capsules")

    print("\nAll tests passed.")
