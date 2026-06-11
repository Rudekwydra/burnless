import argparse
import pytest
from burnless.cli import _worker_overrides_from_args


class TestWorkerOverridesFromArgs:
    """Test the _worker_overrides_from_args helper function."""

    def test_single_override_silver(self):
        """Single override: silver="ollama:gemma4-e4b", others None."""
        args = argparse.Namespace(
            silver="ollama:gemma4-e4b",
            gold=None,
            bronze=None,
            diamond=None,
        )
        result = _worker_overrides_from_args(args)
        assert result == {"silver": "ollama:gemma4-e4b"}

    def test_all_none(self):
        """All tiers None -> empty dict."""
        args = argparse.Namespace(
            silver=None,
            gold=None,
            bronze=None,
            diamond=None,
        )
        result = _worker_overrides_from_args(args)
        assert result == {}

    def test_multiple_overrides(self):
        """Multiple overrides: gold="opus" and silver="codex:gpt-5.2"."""
        args = argparse.Namespace(
            gold="opus",
            silver="codex:gpt-5.2",
            bronze=None,
            diamond=None,
        )
        result = _worker_overrides_from_args(args)
        assert result == {"gold": "opus", "silver": "codex:gpt-5.2"}

    def test_missing_attributes(self):
        """Handle missing attributes gracefully (shouldn't happen but robustness test)."""
        args = argparse.Namespace(silver="test:model")
        # Don't set the others — test that getattr with None default doesn't break
        result = _worker_overrides_from_args(args)
        assert result == {"silver": "test:model"}
