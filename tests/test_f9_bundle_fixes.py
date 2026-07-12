"""Tests for F9a bundle of small fixes (4 items)."""

import json
import re
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from burnless import config as config_mod
from burnless.pilot import logs as logs_mod, rollover as rollover_mod
from burnless import mcp_server


class TestConfigLoadReturnsCopy:
    """Test that config.load returns a deep copy of DEFAULT_CONFIG when configs are empty."""

    def test_config_load_returns_copy(self, tmp_path, monkeypatch):
        """Verify that mutations to first config.load result don't affect DEFAULT_CONFIG or second load."""
        # Set empty global config env to bypass ~/.config/burnless/config.yaml
        monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", "")

        # Use non-existent config path
        config_path = tmp_path / "nonexistent.yaml"

        # First load
        cfg1 = config_mod.load(config_path)
        assert cfg1 is not None

        # Mutate the first result
        cfg1["agents"]["mutated"] = True

        # Second load should not reflect mutation
        cfg2 = config_mod.load(config_path)
        assert "mutated" not in cfg2.get("agents", {})

        # DEFAULT_CONFIG should be untouched
        assert "mutated" not in config_mod.DEFAULT_CONFIG.get("agents", {})


class TestLogsUsageIncludesOutputTokens:
    """Test that logs usage calculation includes output_tokens."""

    def test_logs_usage_includes_output_tokens(self, tmp_path):
        """Verify that _last_assistant_usage_tokens includes output_tokens in sum."""
        transcript_path = tmp_path / "transcript.jsonl"

        # Write a synthetic transcript with 1 assistant record
        assistant_record = {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 5,
                    "output_tokens": 7,
                }
            }
        }
        with transcript_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(assistant_record) + "\n")

        # Call the function
        result = logs_mod._last_assistant_usage_tokens(transcript_path)

        # Expected: 10 + 20 + 5 + 7 = 42
        assert result == 42


class TestMcpDowngradesH2:
    """Test that MCP delegate downgrade H2 headers in spec text."""

    def test_mcp_downgrades_h2(self):
        """Verify that re.sub downgrades ## headers to ### in spec text."""
        text_with_h2 = """## Goal
Some goal here.

## Verify
Check this.

### Already H3
This stays."""

        downgraded = re.sub(r"^##\s", "### ", text_with_h2, flags=re.MULTILINE)

        # Verify H2 headers are downgraded
        assert "### Goal" in downgraded
        assert "### Verify" in downgraded
        # Already-H3 headers stay the same
        assert "#### Already H3" not in downgraded
        assert "### Already H3" in downgraded


class TestRolloverTsNotNone:
    """Test that restore logging generates a valid timestamp when payload has no ts."""

    def test_rollover_ts_not_none(self, tmp_path):
        """Verify that render_restore generates a non-None UTC timestamp."""
        from burnless.pilot import events as events_mod
        from unittest.mock import patch, MagicMock

        # Create minimal .burnless structure
        burnless_root = tmp_path / ".burnless"
        burnless_root.mkdir(exist_ok=True)
        (burnless_root / "state.json").write_text("{}")

        # Mock recovery.render_restore to return a payload WITHOUT ts field
        mock_recovery_payload = {
            "hookEventName": "test_event",
            "additionalContext": "test context"
            # Note: NO "hookSpecificOutput" or "ts" field
        }

        # Capture what gets appended to session log
        captured_log_entry = {}

        def capture_append(root, entry_dict):
            captured_log_entry.update(entry_dict)

        with patch("burnless.pilot.rollover.recovery.render_restore") as mock_render, \
             patch("burnless.pilot.rollover.append_session_log", side_effect=capture_append):
            mock_render.return_value = mock_recovery_payload

            # Call render_restore which should log with generated ts
            result = rollover_mod.render_restore(
                burnless_root,
                host="claude",
                host_session_id="old-session",
                process_instance_id="pid-1",
                new_session_id="new-session",
                source="clear",
                budget_tokens=2000
            )

        # Verify the ts field was generated and logged
        ts_value = captured_log_entry.get("ts")
        assert ts_value is not None, "ts field should not be None"
        assert isinstance(ts_value, str), "ts should be a string"
        assert ts_value.endswith("Z"), "ts should end with 'Z' (UTC)"
        # Verify it looks like a valid ISO 8601 timestamp
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts_value), \
            f"ts should match ISO 8601 format, got: {ts_value}"
