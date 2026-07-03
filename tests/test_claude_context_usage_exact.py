import json
from pathlib import Path

import pytest

from burnless.pilot.logs import claude_context_usage


def test_claude_context_usage_exact_from_transcript(tmp_path):
    """Test that claude_context_usage reads exact usage from transcript when available."""
    # Setup: create events.jsonl with transcript_ref pointing to transcript.jsonl
    transcript_path = tmp_path / "transcript.jsonl"
    events_dir = tmp_path / ".burnless" / "pilot" / "runs" / "run-1"
    events_dir.mkdir(parents=True, exist_ok=True)
    events_jsonl = events_dir / "events.jsonl"

    # Create events.jsonl with transcript_ref in the last line
    with events_jsonl.open("w") as f:
        f.write(json.dumps({"event": "start", "host": "claude"}) + "\n")
        f.write(
            json.dumps(
                {
                    "event": "stop",
                    "host": "claude",
                    "transcript_ref": str(transcript_path),
                }
            )
            + "\n"
        )

    # Create transcript.jsonl with 3 assistant messages with growing usage
    # Line 1: sum = 100 + 200 + 300 = 600
    # Line 2: sum = 500 + 600 + 700 = 1800
    # Line 3: sum = 5000 + 6000 + 7000 = 18000 (EXPECTED result)
    with transcript_path.open("w") as f:
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-sonnet-5",
                        "usage": {
                            "input_tokens": 100,
                            "cache_read_input_tokens": 200,
                            "cache_creation_input_tokens": 300,
                        },
                    },
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-sonnet-5",
                        "usage": {
                            "input_tokens": 500,
                            "cache_read_input_tokens": 600,
                            "cache_creation_input_tokens": 700,
                        },
                    },
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-sonnet-5",
                        "usage": {
                            "input_tokens": 5000,
                            "cache_read_input_tokens": 6000,
                            "cache_creation_input_tokens": 7000,
                        },
                    },
                }
            )
            + "\n"
        )

    # Call the function with exact parameters
    result = claude_context_usage(str(tmp_path), root=tmp_path, run_id="run-1")

    # Verify that current is the sum of the last message's usage
    assert result.current == 18000, f"Expected 18000, got {result.current}"
    assert result.confidence == "exact"


def test_claude_context_usage_fallback_missing_run(tmp_path):
    """Test that claude_context_usage falls back when run_id doesn't exist."""
    # Call the function with a non-existent run_id
    result = claude_context_usage(str(tmp_path), root=tmp_path, run_id="run-inexistente")

    # Verify that it doesn't error and confidence is not "exact"
    assert result.confidence in ("unknown", "estimated")
