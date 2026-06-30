"""Tests for owner-loop telemetry path correctness — no nested burnless directories."""

import tempfile
from pathlib import Path

from burnless.owner_loop import refine_seed, log_owner_event


def test_telemetry_no_nested_burnless():
    """Verify telemetry logs to first-level .burnless, not nested burnless-in-burnless."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        burnless_dir = root / ".burnless"
        burnless_dir.mkdir(parents=True, exist_ok=True)

        log_owner_event(root, {"phase": "test", "result": "ok"})

        log_file = root / ".burnless" / "owner_loop.jsonl"
        assert log_file.exists(), f"Log file not created at {log_file}"

        # Verify NO nested burnless-in-burnless exists
        nested_burnless = burnless_dir / ".burnless"
        assert not nested_burnless.exists(), f"Nested .burnless found at {nested_burnless}"

        # Also check deeper nesting doesn't occur
        for item in burnless_dir.rglob(".burnless"):
            if item != burnless_dir:
                raise AssertionError(f"Nested .burnless found at {item}")


def test_refine_seed_root_none_safe():
    """Verify refine_seed(root=None) doesn't raise and doesn't create nested burnless."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        cache_path = root / ".burnless" / "cache.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        def fake_rewriter(prompt):
            return "refined content"

        result = refine_seed(
            cache_path=str(cache_path),
            predecessors=[("chat1", "content1")],
            floor_md="floor content",
            rewriter=fake_rewriter,
            owner_model="test",
            generated_at="2026-06-29T00:00:00Z",
            root=None,
        )

        # Should not raise; result depends on validation logic
        assert isinstance(result, bool)

        # Verify NO nested burnless-in-burnless was created
        burnless_dir = root / ".burnless"
        if burnless_dir.exists():
            nested = burnless_dir / ".burnless"
            assert not nested.exists(), f"Nested .burnless created at {nested}"
