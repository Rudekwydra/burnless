"""Offline tests for chat display layer: expand_for_display and chat_history.append."""
from __future__ import annotations

import pytest

from burnless.maestro.display import expand_for_display
from burnless import chat_history


# --- expand_for_display ---

class TestExpandForDisplay:
    def test_none_fn_returns_original(self):
        assert expand_for_display("hello", None) == "hello"

    def test_fake_fn_returns_expanded(self):
        def fake(prompt):
            return "expanded text"
        assert expand_for_display("hello", fake) == "expanded text"

    def test_raising_fn_returns_original(self):
        def bad_fn(prompt):
            raise RuntimeError("network down")
        assert expand_for_display("hello", bad_fn) == "hello"

    def test_empty_string_fn_returns_original(self):
        def empty_fn(prompt):
            return ""
        assert expand_for_display("hello", empty_fn) == "hello"

    def test_whitespace_only_fn_returns_original(self):
        def ws_fn(prompt):
            return "   "
        assert expand_for_display("hello", ws_fn) == "hello"

    def test_none_return_fn_returns_original(self):
        def none_fn(prompt):
            return None
        assert expand_for_display("hello", none_fn) == "hello"


# --- chat_history.append ---

class TestChatHistoryAppend:
    def test_creates_file_with_header(self, tmp_path):
        path = tmp_path / "history.md"
        chat_history.append(path, user="hi", burnless="hello")
        content = path.read_text(encoding="utf-8")
        assert content.startswith(chat_history.HEADER)

    def test_accumulates_two_entries(self, tmp_path):
        path = tmp_path / "history.md"
        chat_history.append(path, user="first", burnless="resp1")
        chat_history.append(path, user="second", burnless="resp2")
        content = path.read_text(encoding="utf-8")
        assert "first" in content
        assert "resp1" in content
        assert "second" in content
        assert "resp2" in content
        assert content.count("## ") == 2

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "deep" / "history.md"
        chat_history.append(path, user="u", burnless="b")
        assert path.exists()
