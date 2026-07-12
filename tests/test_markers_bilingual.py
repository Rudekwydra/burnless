"""Golden tests for bilingual markers support (PT/EN dual-read).

Tests verify that:
1. English section aliases normalize to Portuguese canonical keys
2. Bilingual documents parse identically regardless of header language
3. Exchange markers (Q:/A:) work as line-anchored alternatives to PERGUNTA:/RESPOSTA:
4. Short markers like "Q:" don't match inside prose (line-anchored matching)
5. Validators reject documents with en-markers as raw chat transcripts
"""

import pytest
from burnless.markers import normalize_section, find_line_anchored, EXCHANGE_MARKER_LINES
from burnless.epochs_v2 import parse_living, parse_living_v3, is_noop
from burnless.recovery import _validate_candidate


class TestNormalizeSection:
    def test_normalize_section_en_to_pt(self):
        """All EN aliases map to PT canonical; PT and unknown pass through."""
        # English aliases
        assert normalize_section("Current focus") == "Foco atual"
        assert normalize_section("Open threads") == "Threads abertas"
        assert normalize_section("Decisions") == "Decisões"
        assert normalize_section("Risks") == "Riscos"
        assert normalize_section("Last validation") == "Última validação"
        assert normalize_section("Recoverables") == "Recuperáveis"

        # Identity pairs (same in both languages)
        assert normalize_section("Contracts") == "Contracts"
        assert normalize_section("Refs") == "Refs"

        # Portuguese names pass through
        assert normalize_section("Foco atual") == "Foco atual"
        assert normalize_section("Decisões") == "Decisões"

        # Unknown names pass through
        assert normalize_section("Whatever") == "Whatever"


class TestFindLineAnchored:
    def test_find_line_anchored_at_start(self):
        """Marker at text start returns 0."""
        text = "Q: what is this"
        assert find_line_anchored(text, "Q:") == 0

    def test_find_line_anchored_at_line_start(self):
        """Marker at line start returns position after newline."""
        text = "some text\nQ: question here"
        assert find_line_anchored(text, "Q:") == 10  # position of 'Q'

    def test_find_line_anchored_mid_line_not_found(self):
        """Marker in middle of line returns -1 (not line-anchored)."""
        text = "see the FAQ: here"
        assert find_line_anchored(text, "Q:") == -1

    def test_find_line_anchored_absent(self):
        """Absent marker returns -1."""
        text = "no markers here"
        assert find_line_anchored(text, "Q:") == -1


class TestParseLivingV3Bilingual:
    def test_parse_living_v3_bilingual_golden(self):
        """PT and EN headers parse identically into canonical PT keys."""
        # Build body content (same for both versions)
        body_items = {
            "Foco atual": ["item1", "item2"],
            "Threads abertas": ["thread1"],
            "Decisões": ["decision1"],
            "Contracts": ["contract1"],
            "Refs": ["ref1"],
            "Riscos": ["risk1"],
            "Última validação": ["validation1"],
            "Recuperáveis": ["recoverable1"],
        }

        # PT version
        pt_doc = ""
        for section, items in body_items.items():
            pt_doc += f"## {section}\n"
            for item in items:
                pt_doc += f"- {item}\n"
            pt_doc += "\n"

        # EN version (same items, EN headers)
        en_doc = ""
        en_headers = {
            "Foco atual": "Current focus",
            "Threads abertas": "Open threads",
            "Decisões": "Decisions",
            "Riscos": "Risks",
            "Última validação": "Last validation",
            "Recuperáveis": "Recoverables",
        }
        for section, items in body_items.items():
            en_section = en_headers.get(section, section)  # identity for Contracts/Refs
            en_doc += f"## {en_section}\n"
            for item in items:
                en_doc += f"- {item}\n"
            en_doc += "\n"

        # Parse both and compare
        pt_parsed = parse_living_v3(pt_doc)
        en_parsed = parse_living_v3(en_doc)

        assert pt_parsed == en_parsed, "PT and EN docs should parse identically"

        # Verify canonical PT keys are present with expected content
        assert pt_parsed["Foco atual"] == ["item1", "item2"]
        assert pt_parsed["Riscos"] == ["risk1"]
        assert pt_parsed["Última validação"] == ["validation1"]


class TestParseLivingBilingual:
    def test_parse_living_bilingual_golden(self):
        """V2 parser (5 sections) also handles bilingual headers."""
        body_items = {
            "Foco atual": ["item1"],
            "Threads abertas": ["thread1"],
            "Decisões": ["decision1"],
            "Contracts": ["contract1"],
            "Refs": ["ref1"],
        }

        # PT version
        pt_doc = ""
        for section, items in body_items.items():
            pt_doc += f"## {section}\n"
            for item in items:
                pt_doc += f"- {item}\n"
            pt_doc += "\n"

        # EN version
        en_doc = ""
        en_headers = {
            "Foco atual": "Current focus",
            "Threads abertas": "Open threads",
            "Decisões": "Decisions",
        }
        for section, items in body_items.items():
            en_section = en_headers.get(section, section)
            en_doc += f"## {en_section}\n"
            for item in items:
                en_doc += f"- {item}\n"
            en_doc += "\n"

        pt_parsed = parse_living(pt_doc)
        en_parsed = parse_living(en_doc)

        assert pt_parsed == en_parsed
        assert pt_parsed["Foco atual"] == ["item1"]


class TestExchangeMarkersEN:
    def test_exchange_markers_en(self):
        """Q:/A: markers work identically to PERGUNTA:/RESPOSTA:."""
        # PT version
        pt_exchange = (
            "PERGUNTA: what is the answer?\n"
            "RESPOSTA: the answer is 42"
        )

        # EN version
        en_exchange = (
            "Q: what is the answer?\n"
            "A: the answer is 42"
        )

        # Both should extract same user portion and have same triviality
        pt_is_noop = is_noop("prev doc", pt_exchange, max_len=500)
        en_is_noop = is_noop("prev doc", en_exchange, max_len=500)

        assert pt_is_noop == en_is_noop


class TestExchangeMarkerLineAnchored:
    def test_exchange_marker_not_matched_mid_line(self):
        """FAQ: in prose should not trigger Q: marker match."""
        # Text with "Q:" buried in prose, not line-anchored
        text = "see the FAQ: here for more info"

        # find_line_anchored returns -1 for non-line-anchored "Q:"
        assert find_line_anchored(text, "Q:") == -1


class TestValidateCandidateRejectsENMarkers:
    def test_validate_candidate_rejects_en_markers(self):
        """_validate_candidate rejects docs with Q: standalone lines."""
        # Minimal valid candidate (has at least one section to pass no_recognized_sections check)
        # Note: Q: and A: must be exact standalone lines (not "Q: text")
        candidate = (
            "## Foco atual\n"
            "- some item\n"
            "Q:\n"
            "what is this?\n"
            "A:\n"
            "yes it is"
        )

        is_valid, reason = _validate_candidate(candidate, prev_md="", pending=[])

        assert not is_valid, "Candidate with Q: line should be rejected"
        assert reason == "chat_completion_markers"


class TestExchangeMarkerLinesConstant:
    def test_exchange_marker_lines_constant(self):
        """EXCHANGE_MARKER_LINES contains all standalone markers."""
        assert "PERGUNTA:" in EXCHANGE_MARKER_LINES
        assert "RESPOSTA:" in EXCHANGE_MARKER_LINES
        assert "Q:" in EXCHANGE_MARKER_LINES
        assert "A:" in EXCHANGE_MARKER_LINES
