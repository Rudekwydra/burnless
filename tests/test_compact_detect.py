from __future__ import annotations

import json
from pathlib import Path

import pytest

from burnless.pilot.compact_detect import (
    CompactSummary,
    detect_compact_summaries,
    has_genuine_compact,
)


def test_genuine_string_content(tmp_path: Path) -> None:
    """Test a genuine compact summary with string content."""
    transcript = tmp_path / "transcript.jsonl"
    record = {
        "isCompactSummary": True,
        "type": "user",
        "sessionId": "s1",
        "uuid": "u1",
        "parentUuid": "p0",
        "message": {"role": "user", "content": "SUMMARY TEXT"},
    }
    transcript.write_text(json.dumps(record) + "\n")

    summaries = detect_compact_summaries(transcript)
    assert len(summaries) == 1
    assert summaries[0].session_id == "s1"
    assert summaries[0].summary_text == "SUMMARY TEXT"
    assert summaries[0].type == "user"
    assert summaries[0].uuid == "u1"
    assert summaries[0].parent_uuid == "p0"
    assert has_genuine_compact(transcript) is True


def test_genuine_list_content(tmp_path: Path) -> None:
    """Test a genuine compact summary with list-of-blocks content."""
    transcript = tmp_path / "transcript.jsonl"
    record = {
        "isCompactSummary": True,
        "type": "user",
        "sessionId": "s2",
        "uuid": "u2",
        "parentUuid": "p1",
        "message": {
            "role": "user",
            "content": [
                {"type": "text", "text": "A"},
                {"type": "text", "text": "B"},
            ],
        },
    }
    transcript.write_text(json.dumps(record) + "\n")

    summaries = detect_compact_summaries(transcript)
    assert len(summaries) == 1
    assert summaries[0].summary_text == "A\nB"
    assert has_genuine_compact(transcript) is True


def test_no_compact_record(tmp_path: Path) -> None:
    """Test a file with no compact summary records."""
    transcript = tmp_path / "transcript.jsonl"
    record1 = {"type": "user", "message": "normal user msg"}
    record2 = {"type": "assistant", "message": "normal assistant msg"}
    transcript.write_text(
        json.dumps(record1) + "\n" + json.dumps(record2) + "\n"
    )

    summaries = detect_compact_summaries(transcript)
    assert summaries == []
    assert has_genuine_compact(transcript) is False


def test_isCompactSummary_but_non_user_type_is_not_genuine(
    tmp_path: Path,
) -> None:
    """Test that a non-user type with isCompactSummary is detected but not genuine."""
    transcript = tmp_path / "transcript.jsonl"
    record = {
        "isCompactSummary": True,
        "type": "summary",  # fork/tamper shape, not "user"
        "sessionId": "s3",
        "uuid": "u3",
        "parentUuid": "p2",
        "message": {"role": "user", "content": "TAMPERED SUMMARY"},
    }
    transcript.write_text(json.dumps(record) + "\n")

    summaries = detect_compact_summaries(transcript)
    assert len(summaries) == 1  # detected
    assert summaries[0].type == "summary"
    assert has_genuine_compact(transcript) is False  # not genuine


def test_robust_to_malformed_lines(tmp_path: Path) -> None:
    """Test robustness to blank, invalid JSON, and invalid UTF-8 lines."""
    transcript = tmp_path / "transcript.jsonl"

    # Build file with: blank line, invalid json, invalid utf-8 bytes, then genuine record
    lines = []
    lines.append("")  # line 0: blank
    lines.append("not valid json")  # line 1: invalid json
    # line 2: will be written as bytes (invalid utf-8)

    with open(transcript, "wb") as f:
        # Write first two lines as text
        f.write((lines[0] + "\n").encode("utf-8"))
        f.write((lines[1] + "\n").encode("utf-8"))
        # Write invalid UTF-8 bytes
        f.write(b"\x80\x81\n")
        # Write genuine record on line 3
        genuine = {
            "isCompactSummary": True,
            "type": "user",
            "sessionId": "s4",
            "uuid": "u4",
            "parentUuid": "p3",
            "message": {"role": "user", "content": "RECOVERED"},
        }
        f.write((json.dumps(genuine) + "\n").encode("utf-8"))

    # Should not raise, find 1 genuine record at line 3
    summaries = detect_compact_summaries(transcript)
    assert len(summaries) == 1
    assert summaries[0].summary_text == "RECOVERED"
    assert summaries[0].line_index == 3
    assert has_genuine_compact(transcript) is True


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    """Test that a missing file returns empty list and has_genuine_compact is False."""
    missing = tmp_path / "does_not_exist.jsonl"
    assert detect_compact_summaries(missing) == []
    assert has_genuine_compact(missing) is False
