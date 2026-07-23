#!/usr/bin/env python3
"""
Pytest tests for chapter_output_guard validation.
"""
import sys
from pathlib import Path

import pytest

# Insert the scripts directory into sys.path to import the guard module
_BOOK_SCRIPTS = Path(__file__).resolve().parents[1] / "book" / "scripts"
pytestmark = pytest.mark.skipif(not _BOOK_SCRIPTS.exists(), reason="book/ not present in this checkout")
sys.path.insert(0, str(_BOOK_SCRIPTS))

try:
    from chapter_output_guard import validate_chapter_output
except ImportError:
    validate_chapter_output = None


class TestChapterOutputGuard:
    """Test suite for validate_chapter_output function."""

    def test_meta_output_rejected(self):
        """Meta-output stub should be rejected."""
        meta_stub = "Capítulo 9 escrito em `book/chapters/chapter_09.md` — ~2100 palavras. Arco em três atos: nutri B26 (worker OK com dead code, Roberto corta com 'C'), a regra escrita em `CLAUDE.md`, e três semanas depois o giro de eixo — o auditor cego em outputs >100k, `d270` diagnosticando o próprio bug enquanto cai nele, root cause na linha 147 do `live_runner.py` (substring dedup que devia ser sufixo), e a ironia final do `burnless_delegation_guard.sh` bloqueando o `d271` que ia consertar o auditor. Fechamento sem lição em bullet — só a frase seca do Roberto e a nota de que 'confiar em status virou o erro mais caro do mês'."
        # Pad to ensure we check meta-output detection before size check
        meta_stub += "\n\nExtra content. " * 100
        is_valid, reason = validate_chapter_output(meta_stub, 9)
        assert is_valid is False, f"expected invalid, got: {reason}"
        assert "meta-output" in reason.lower() or "begins" in reason.lower()

    def test_short_text_rejected(self):
        """Text shorter than 1500 bytes should be rejected."""
        short_text = "# Capítulo 9 — Test\n\nParagraph one.\n\nParagraph two.\n\nParagraph three."
        is_valid, reason = validate_chapter_output(short_text, 9)
        assert is_valid is False
        assert "short" in reason.lower()

    def test_missing_chapter_heading_rejected(self):
        """Missing or incorrect chapter heading should be rejected."""
        # No heading at all
        no_heading = "Paragraph one is here.\n\n" * 100  # enough bytes
        is_valid, reason = validate_chapter_output(no_heading, 9)
        assert is_valid is False
        assert "heading" in reason.lower() or "first line" in reason.lower()

        # Wrong chapter number
        wrong_ch = "# Capítulo 8 — Some Title\n\n" + ("Paragraph with content.\n\n" * 100)
        is_valid, reason = validate_chapter_output(wrong_ch, 9)
        assert is_valid is False

    def test_insufficient_paragraphs_rejected(self):
        """Fewer than 3 paragraphs should be rejected."""
        two_paras = "# Capítulo 9 — Title\n\nParagraph one with enough content to fill space for testing purposes.\n\nParagraph two also has content here.\n"
        # Pad to > 1500 bytes
        two_paras += "x" * 1600
        is_valid, reason = validate_chapter_output(two_paras, 9)
        assert is_valid is False
        assert "paragraph" in reason.lower()

    def test_valid_chapter_accepted(self):
        """Valid chapter with proper structure should be accepted."""
        valid_chapter = "# Capítulo 9 — Worker PART, auditor cego\n\n"
        valid_chapter += "Paragraph one with some content describing the arc and tension of the chapter. "
        valid_chapter += "This is a narrative paragraph explaining the situation and the lesson learned.\n\n"
        valid_chapter += "Paragraph two continuing the story, providing more context and detail. "
        valid_chapter += "The chapter explores a specific incident or pattern in depth.\n\n"
        valid_chapter += "Paragraph three concluding the arc, tying together the narrative threads. "
        valid_chapter += "The final paragraph synthesizes the lesson and its implications.\n\n"
        # Pad to ensure >= 1500 bytes
        valid_chapter += "Additional paragraph to meet length requirement. " * 50

        is_valid, reason = validate_chapter_output(valid_chapter, 9)
        assert is_valid is True, f"expected valid, got reason: {reason}"
        assert reason == ""

    def test_alternative_chapter_heading_em_dash(self):
        """Chapter heading with em-dash should also be valid."""
        valid_with_emdash = "# Capítulo 9 — Worker PART, auditor cego\n\n"
        valid_with_emdash += ("Substantial paragraph content that goes on and on to meet minimum byte requirements. " * 50)
        valid_with_emdash += "\n\nSecond paragraph with more content. " * 50
        valid_with_emdash += "\n\nThird paragraph to ensure we have enough paragraphs. " * 50

        is_valid, reason = validate_chapter_output(valid_with_emdash, 9)
        assert is_valid is True, f"expected valid with em-dash, got: {reason}"

    def test_alternative_chapter_heading_hyphen(self):
        """Chapter heading with hyphen should also be valid."""
        valid_with_hyphen = "# Capítulo 9 - Worker PART, auditor cego\n\n"
        valid_with_hyphen += ("Substantial paragraph content that goes on and on to meet minimum byte requirements. " * 50)
        valid_with_hyphen += "\n\nSecond paragraph with more content. " * 50
        valid_with_hyphen += "\n\nThird paragraph to ensure we have enough paragraphs. " * 50

        is_valid, reason = validate_chapter_output(valid_with_hyphen, 9)
        assert is_valid is True, f"expected valid with hyphen, got: {reason}"

    def test_meta_marker_in_content(self):
        """Meta-markers anywhere in first 1200 chars should be rejected."""
        with_marker = "# Capítulo 9 — Test\n\n"
        with_marker += "Some content here. "
        with_marker += "Arco em três atos is mentioned in the text. "
        with_marker += "More content to fill space. " * 100  # Increase repetitions to ensure >= 1500 bytes

        is_valid, reason = validate_chapter_output(with_marker, 9)
        assert is_valid is False
        assert "marker" in reason.lower()

    def test_word_count_pattern_marker(self):
        """The ~<number> palavras pattern should be rejected."""
        with_pattern = "# Capítulo 9 — Test\n\n"
        with_pattern += "Content here. This chapter is ~2500 palavras in length. "
        with_pattern += "More content to fill the rest. " * 50

        is_valid, reason = validate_chapter_output(with_pattern, 9)
        assert is_valid is False
        assert "pattern" in reason.lower() or "marker" in reason.lower()
