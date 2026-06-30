import pytest
from burnless.owner_validate import validate_owner_output, _normalize_core


def test_normalize_core_basic():
    """Basic normalization: bullet removal."""
    assert _normalize_core("- fix the parser bug") == "fix the parser bug"
    assert _normalize_core("## Decisões") == ""
    assert _normalize_core("") == ""


def test_normalize_core_trust_tags():
    """Removal of trust tags at start."""
    assert _normalize_core("- [doctrine] use async/await") == "use async/await"
    assert _normalize_core("- [state] db is locked") == "db is locked"
    assert _normalize_core("- [inflight] work in progress") == "work in progress"


def test_normalize_core_provenance():
    """Removal of chat provenance at end."""
    assert _normalize_core("- fix parser bug [chat:ab12·t3]") == "fix parser bug"
    assert _normalize_core("- fix parser bug [chat:xyz] ") == "fix parser bug"


def test_normalize_core_supersede():
    """Supersede marker removal (~~text~~ → text)."""
    assert _normalize_core("- ~~old approach~~ [chat:x]") == "old approach"
    assert _normalize_core("- ~~use mtime~~") == "use mtime"


def test_normalize_core_case_insensitive():
    """Case insensitivity (all lower)."""
    assert _normalize_core("- FIX THE PARSER BUG") == "fix the parser bug"


def test_accepts_pure_retag_and_move():
    """Floor line moved to different section with tags. validate returns CANDIDATE."""
    floor = "## Decisões\n- fix the parser bug"
    candidate = "## Threads abertas\n- [inflight] fix the parser bug [chat:ab12·t3]"
    assert validate_owner_output(floor, candidate) == candidate


def test_rejects_hallucinated_line():
    """Candidate has 1 hallucinated line among supported ones. Drops hallucinated, keeps supported."""
    floor = "## Decisões\n- fix the parser bug\n- add logging\n- fix parsing tests"
    candidate = "## Decisões\n- fix the parser bug [chat:x]\n- add logging\n- deploy to production on friday\n- fix parsing tests"
    result = validate_owner_output(floor, candidate)
    # Should have the 3 supported lines but NOT the hallucinated one
    assert "fix the parser bug" in result
    assert "add logging" in result
    assert "fix parsing tests" in result
    assert "deploy to production on friday" not in result
    # Result should NOT be equal to floor (due to provenance tag)
    assert result != floor


def test_accepts_dedup_and_compact():
    """Candidate is subset/compacted of floor (dedup). validate returns CANDIDATE."""
    floor = "## Decisões\n- fix the parser bug\n- add logging\n- fix the parser bug"
    candidate = "## Decisões\n- fix the parser bug\n- add logging"
    assert validate_owner_output(floor, candidate) == candidate


def test_supersede_marker_supported():
    """Supersede content nucleus is validated. validate returns CANDIDATE."""
    floor = "- use mtime for cache"
    candidate = "- ~~use mtime for cache~~ [chat:x·t1]"
    assert validate_owner_output(floor, candidate) == candidate


def test_headers_and_blanks_ignored():
    """Reorganizing headers/blanks without new content. validate returns CANDIDATE."""
    floor = "## Decisões\n- fix bug\n\n## Threads\n- open thread"
    candidate = "## Threads\n- open thread\n\n## Decisões\n- fix bug"
    assert validate_owner_output(floor, candidate) == candidate


def test_error_returns_floor():
    """Invalid input (None, wrong type) returns FLOOR without exception."""
    floor = "## Decisões\n- fix bug"
    assert validate_owner_output(floor, None) == floor
    assert validate_owner_output(floor, 123) == floor


def test_drops_only_unsupported_line():
    """Candidate with 4 supported + 1 unsupported. Returns the 4 supported."""
    floor = "## Decisões\n- alpha keep\n- beta keep\n- gamma keep\n- delta keep"
    candidate = "## Decisões\n- alpha keep\n- beta keep\n- gamma keep\n- delta keep\n- ship rocket mars"
    result = validate_owner_output(floor, candidate)
    assert "alpha keep" in result
    assert "beta keep" in result
    assert "gamma keep" in result
    assert "delta keep" in result
    assert "ship rocket mars" not in result


def test_degenerate_falls_back_to_floor():
    """Candidate where <25% lines have support. Falls back to floor."""
    floor = "## D\n- alpha\n- beta\n- gamma\n- delta\n- echo\n- foxtrot\n- golf\n- hotel"
    candidate = "## D\n- alpha\n- nonsense1\n- nonsense2\n- nonsense3\n- nonsense4"
    result = validate_owner_output(floor, candidate)
    assert result == floor


def test_empty_candidate_returns_floor():
    """Empty or None candidate returns floor."""
    floor = "## Decisões\n- fix bug"
    assert validate_owner_output(floor, "") == floor
    assert validate_owner_output(floor, None) == floor
