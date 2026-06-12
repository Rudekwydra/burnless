"""Offline tests for chat per-turn router (no network, no LLM)."""
from __future__ import annotations

import pytest

from burnless.maestro.turn_router import classify_turn, local_answer
from burnless.maestro.engine import PartnerState


# --- classify_turn ---

class TestClassifyTurnLocal:
    def test_plain_ack(self):
        assert classify_turn("ok") == "local"

    def test_short_followup(self):
        assert classify_turn("e dai?") == "local"

    def test_status_question(self):
        assert classify_turn("qual o status?") == "local"

    def test_short_affirmation(self):
        assert classify_turn("entendi, obrigado") == "local"

    def test_single_sentence_question(self):
        assert classify_turn("quanto custa?") == "local"


class TestClassifyTurnMaestro:
    def test_action_word_implementa(self):
        assert classify_turn("implementa o modulo X com testes") == "maestro"

    def test_action_word_cria(self):
        assert classify_turn("cria uma rota nova") == "maestro"

    def test_action_word_build(self):
        assert classify_turn("build the project") == "maestro"

    def test_action_word_fix(self):
        assert classify_turn("fix the bug in parser.py") == "maestro"

    def test_action_word_deploy(self):
        assert classify_turn("deploy to staging") == "maestro"

    def test_action_word_commit(self):
        assert classify_turn("commit these changes") == "maestro"

    def test_action_word_push(self):
        assert classify_turn("push to main") == "maestro"

    def test_action_word_delegate(self):
        assert classify_turn("delegate the task to silver") == "maestro"

    def test_fence_present(self):
        assert classify_turn("```python\nprint('hi')\n```") == "maestro"

    def test_slash_command(self):
        assert classify_turn("/exit") == "maestro"

    def test_too_long(self):
        long_text = "a" * 120
        assert classify_turn(long_text) == "maestro"

    def test_exactly_120_chars_is_maestro(self):
        text = "x" * 120
        assert classify_turn(text) == "maestro"

    def test_question_with_multi_sentence(self):
        assert classify_turn("pode confirmar? e qual o próximo passo?") == "maestro"

    def test_case_insensitive_action(self):
        assert classify_turn("Implementa o handler") == "maestro"
        assert classify_turn("CRIAR o arquivo") == "maestro"


# --- local_answer ---

class TestLocalAnswer:
    def _make_state(self):
        return PartnerState()

    def test_returns_text_when_ollama_fn_returns(self):
        state = self._make_state()
        fake_fn = lambda prompt: "resposta local"
        result = local_answer(state, "ok", ollama_fn=fake_fn)
        assert result == "resposta local"

    def test_prompt_contains_user_text(self):
        state = self._make_state()
        captured = []
        def fake_fn(prompt):
            captured.append(prompt)
            return "ok"
        local_answer(state, "qual o status?", ollama_fn=fake_fn)
        assert "qual o status?" in captured[0]
        assert "1-3 frases" in captured[0]

    def test_returns_none_when_ollama_fn_is_none(self):
        state = self._make_state()
        result = local_answer(state, "ok", ollama_fn=None)
        assert result is None

    def test_returns_none_when_ollama_fn_raises(self):
        state = self._make_state()
        def failing_fn(prompt):
            raise ConnectionRefusedError("ollama not running")
        result = local_answer(state, "ok", ollama_fn=failing_fn)
        assert result is None

    def test_returns_none_on_runtime_error(self):
        state = self._make_state()
        def bad_fn(prompt):
            raise RuntimeError("unexpected")
        result = local_answer(state, "e dai?", ollama_fn=bad_fn)
        assert result is None
