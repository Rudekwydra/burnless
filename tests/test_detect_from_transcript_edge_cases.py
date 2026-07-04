from __future__ import annotations

from burnless.epochs import _detect_from_transcript


def test_invalid_utf8_bytes_are_ignored(tmp_path):
    workspace = tmp_path / "antigravity"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    good_line = f"edited {workspace}/RealProj/main.py\n".encode("utf-8")
    bad_bytes = b"\xff\xfe garbage \x80\x81 line\n"
    transcript.write_bytes((good_line + bad_bytes) * 6)
    result = _detect_from_transcript(transcript, workspace)
    assert result == workspace / "RealProj"


def test_prefix_collision_does_not_merge_projects(tmp_path):
    workspace = tmp_path / "antigravity"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    lines = (f"{workspace}/burnless/src/main.py\n" * 6) + (f"{workspace}/burnless2/src/main.py\n" * 3)
    transcript.write_text(lines, encoding="utf-8")
    result = _detect_from_transcript(transcript, workspace)
    assert result == workspace / "burnless"


def test_giant_single_line_does_not_hang(tmp_path):
    workspace = tmp_path / "antigravity"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    filler = "x" * 200_000
    line = f'{{"noise":"{filler}","file":"{workspace}/RealProj/a.py"}}'
    transcript.write_text((line + "\n") * 6, encoding="utf-8")
    result = _detect_from_transcript(transcript, workspace)
    assert result == workspace / "RealProj"


def test_path_at_exact_eof_no_trailing_newline(tmp_path):
    workspace = tmp_path / "antigravity"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    body = (f"touched {workspace}/RealProj/file.py\n" * 5) + f"{workspace}/RealProj"
    transcript.write_text(body, encoding="utf-8")
    result = _detect_from_transcript(transcript, workspace)
    assert result == workspace / "RealProj"


def test_below_threshold_returns_none(tmp_path):
    workspace = tmp_path / "antigravity"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(f"{workspace}/RealProj/file.py\n" * 4, encoding="utf-8")
    result = _detect_from_transcript(transcript, workspace)
    assert result is None


def test_exactly_five_hits_meets_threshold(tmp_path):
    workspace = tmp_path / "antigravity"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(f"{workspace}/RealProj/file.py\n" * 5, encoding="utf-8")
    result = _detect_from_transcript(transcript, workspace)
    assert result == workspace / "RealProj"


def test_json_garbage_line_ignored_confirmed_regression(tmp_path):
    workspace = tmp_path / "antigravity"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    garbage = f'{{"cwd":"{workspace}/burnless","sessionId":"abc"}}\n' * 6
    real = f"{workspace}/RealProj/main.py\n" * 6
    transcript.write_text(garbage + real, encoding="utf-8")
    result = _detect_from_transcript(transcript, workspace)
    assert result == workspace / "RealProj"


def test_second_occurrence_same_line_not_double_counted(tmp_path):
    """Two references to the workspace on the SAME line: only the first
    occurrence's remainder is inspected. Documents current behavior (one
    count per line ceiling) rather than a silent undercount surprise."""
    workspace = tmp_path / "antigravity"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    line = f"{workspace}/RealProj/a.py and also {workspace}/RealProj/b.py\n"
    transcript.write_text(line * 5, encoding="utf-8")
    result = _detect_from_transcript(transcript, workspace)
    assert result == workspace / "RealProj"


def test_empty_transcript_returns_none(tmp_path):
    workspace = tmp_path / "antigravity"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")
    result = _detect_from_transcript(transcript, workspace)
    assert result is None


def test_nonexistent_transcript_returns_none(tmp_path):
    workspace = tmp_path / "antigravity"
    workspace.mkdir()
    transcript = tmp_path / "does_not_exist.jsonl"
    result = _detect_from_transcript(transcript, workspace)
    assert result is None


def test_realistic_kebab_case_project_name_accepted(tmp_path):
    """Regression guard for the _SAFE_PROJ_NAME_RE fix: real project names
    in this ecosystem are kebab-case with hyphens (e.g. fw-social-next) and
    must still be accepted, not rejected as unsafe."""
    workspace = tmp_path / "antigravity"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(f"{workspace}/fw-social-next/src/app.py\n" * 5, encoding="utf-8")
    result = _detect_from_transcript(transcript, workspace)
    assert result == workspace / "fw-social-next"
