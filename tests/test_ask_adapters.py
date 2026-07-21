from __future__ import annotations

import hashlib
import json
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from burnless.providers import CodexAdapter, AnthropicAdapter, OllamaAdapter, get_adapter
from burnless.providers.contracts import AskRequest, ProviderResult, ResolvedAskTarget
from burnless.pure_ask import run_ask


class TestGetAdapter:
    def test_returns_anthropic_adapter(self):
        assert isinstance(get_adapter("anthropic"), AnthropicAdapter)

    def test_returns_ollama_adapter_for_both_aliases(self):
        assert isinstance(get_adapter("ollama"), OllamaAdapter)
        assert isinstance(get_adapter("ollama-local"), OllamaAdapter)

    def test_returns_codex_adapter(self):
        assert isinstance(get_adapter("codex"), CodexAdapter)

    def test_returns_none_for_unsupported_provider(self):
        assert get_adapter("gemini") is None


class TestAnthropicAdapter:
    def test_resolve_uses_tier_model(self):
        cfg = {"agents": {"gold": {"provider": "anthropic", "model": "claude-opus-4-8"}}}
        adapter = AnthropicAdapter()
        request = AskRequest(prompt="hi", tier="gold")
        target = adapter.resolve(request, cfg)
        assert target.provider == "anthropic"
        assert target.model == "claude-opus-4-8"
        assert target.adapter_key == "anthropic"

    def test_resolve_explicit_model_bypasses_tier_model(self):
        cfg = {"agents": {"gold": {"provider": "anthropic", "model": "claude-opus-4-8"}}}
        adapter = AnthropicAdapter()
        request = AskRequest(prompt="hi", tier="gold", model="claude-sonnet-5-explicit")
        target = adapter.resolve(request, cfg)
        assert target.model == "claude-sonnet-5-explicit"

    def test_invoke_text_calls_claude_cli(self):
        adapter = AnthropicAdapter()
        request = AskRequest(prompt="hi", tier="gold")
        target = ResolvedAskTarget(effective_tier="gold", provider="anthropic", model="claude-opus-4-8")
        with patch("burnless.providers.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = adapter.invoke_text(request, target)
        assert result.returncode == 0
        assert result.stdout == "ok"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "--model" in cmd

    def test_invoke_text_timeout_returns_result_not_exception(self):
        adapter = AnthropicAdapter()
        request = AskRequest(prompt="hi", tier="gold", timeout_s=5)
        target = ResolvedAskTarget(effective_tier="gold", provider="anthropic", model="claude-opus-4-8")
        with patch(
            "burnless.providers.anthropic_adapter.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=5),
        ):
            result = adapter.invoke_text(request, target)
        assert result.timed_out is True
        assert result.returncode == 1

    def test_parse_usage_estimate_when_not_json(self):
        adapter = AnthropicAdapter()
        target = ResolvedAskTarget(effective_tier="gold", provider="anthropic", model="claude-opus-4-8")
        result = ProviderResult(returncode=0, stdout="plain text response")
        usage = adapter.parse_usage(result, target)
        assert usage.basis == "estimate"

    def test_capabilities(self):
        adapter = AnthropicAdapter()
        target = ResolvedAskTarget(effective_tier="gold", provider="anthropic", model="claude-opus-4-8")
        caps = adapter.capabilities(target)
        assert caps.observable_token_usage is True
        assert caps.hard_spend_cap is True

    def test_cancel_returns_false(self):
        assert AnthropicAdapter().cancel() is False


class TestOllamaAdapter:
    def test_resolve(self):
        cfg = {"agents": {"bronze": {"provider": "ollama-local", "model": "gemma-4-eb"}}}
        adapter = OllamaAdapter()
        request = AskRequest(prompt="hi", tier="bronze")
        target = adapter.resolve(request, cfg)
        assert target.provider == "ollama"
        assert target.model == "gemma-4-eb"
        assert target.adapter_key == "ollama"

    def test_invoke_text_delegates_to_run_ask_ollama(self):
        adapter = OllamaAdapter()
        request = AskRequest(prompt="hi", tier="bronze")
        target = ResolvedAskTarget(effective_tier="bronze", provider="ollama", model="gemma-4-eb")
        with patch(
            "burnless.providers.ollama_adapter.pure_ask.run_ask_ollama",
            return_value=(0, "hello", ""),
        ) as mock_ollama:
            result = adapter.invoke_text(request, target)
        mock_ollama.assert_called_once()
        assert result.returncode == 0
        assert result.stdout == "hello"

    def test_parse_usage_always_estimate(self):
        adapter = OllamaAdapter()
        target = ResolvedAskTarget(effective_tier="bronze", provider="ollama", model="gemma-4-eb")
        result = ProviderResult(returncode=0, stdout="hello")
        usage = adapter.parse_usage(result, target)
        assert usage.basis == "estimate"

    def test_capabilities_no_observable_usage(self):
        adapter = OllamaAdapter()
        target = ResolvedAskTarget(effective_tier="bronze", provider="ollama", model="gemma-4-eb")
        caps = adapter.capabilities(target)
        assert caps.observable_token_usage is False
        assert caps.hard_spend_cap is False


class TestCodexAdapterGuards:
    def test_recursion_guard_blocks_without_subprocess(self):
        adapter = CodexAdapter()
        request = AskRequest(prompt="hi", tier="diamond")
        target = ResolvedAskTarget(effective_tier="diamond", provider="codex", model="gpt-5.6-sol", auth="subscription")
        with patch.dict(os.environ, {"BURNLESS_ASK_ACTIVE_PROVIDER": "codex"}):
            with patch("burnless.providers.codex_adapter.subprocess.run") as mock_run:
                result = adapter.invoke_text(request, target)
        mock_run.assert_not_called()
        assert result.returncode == 1
        assert "recursion guard" in result.stderr

    def test_api_auth_raises_before_any_subprocess(self):
        adapter = CodexAdapter()
        request = AskRequest(prompt="hi", tier="diamond")
        target = ResolvedAskTarget(effective_tier="diamond", provider="codex", model="gpt-5.6-sol", auth="api")
        with patch("burnless.providers.codex_adapter.subprocess.run") as mock_run:
            with pytest.raises(RuntimeError, match="codex api transport not wired yet"):
                adapter.invoke_text(request, target)
        mock_run.assert_not_called()

    def test_binary_not_found_returns_result_not_exception(self):
        adapter = CodexAdapter()
        request = AskRequest(prompt="hi", tier="diamond")
        target = ResolvedAskTarget(effective_tier="diamond", provider="codex", model="gpt-5.6-sol", auth="subscription")
        with patch("burnless.providers.codex_adapter.shutil.which", return_value=None):
            with patch("burnless.providers.codex_adapter.subprocess.run") as mock_run:
                result = adapter.invoke_text(request, target)
        mock_run.assert_not_called()
        assert result.returncode == 1
        assert "not found in PATH" in result.stderr

    def test_successful_invoke_builds_codex_exec_command(self):
        adapter = CodexAdapter()
        request = AskRequest(prompt="hi", tier="diamond", effort="high")
        target = ResolvedAskTarget(effective_tier="diamond", provider="codex", model="gpt-5.6-sol", auth="subscription")
        with patch("burnless.providers.codex_adapter.shutil.which", return_value="/usr/local/bin/codex"):
            with patch("burnless.providers.codex_adapter.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                result = adapter.invoke_text(request, target)
        assert result.returncode == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/local/bin/codex"
        assert cmd[1] == "exec"
        assert "-m" in cmd
        assert "gpt-5.6-sol" in cmd
        assert "hi" in cmd
        env = mock_run.call_args[1]["env"]
        assert env["BURNLESS_ASK_ACTIVE_PROVIDER"] == "codex"

    def test_parse_usage_provider_reported(self):
        adapter = CodexAdapter()
        target = ResolvedAskTarget(effective_tier="diamond", provider="codex", model="gpt-5.6-sol")
        stdout = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
        result = ProviderResult(returncode=0, stdout=stdout)
        usage = adapter.parse_usage(result, target)
        assert usage.basis == "provider_reported"
        assert usage.input_tokens == 10
        assert usage.output_tokens == 5

    def test_parse_usage_estimate_fallback(self):
        adapter = CodexAdapter()
        target = ResolvedAskTarget(effective_tier="diamond", provider="codex", model="gpt-5.6-sol")
        result = ProviderResult(returncode=0, stdout="not json at all")
        usage = adapter.parse_usage(result, target)
        assert usage.basis == "estimate"


class TestRunAskProviderDispatch:
    """The king test: provider="codex" must never touch the claude CLI path."""

    def test_provider_codex_never_calls_claude_cli(self):
        cfg = {"agents": {"gold": {"provider": "codex", "command": "codex exec -m gpt-5.6-sol"}}}
        with patch("burnless.pure_ask.subprocess.run") as mock_claude_run:
            with patch("burnless.providers.codex_adapter.shutil.which", return_value="/usr/local/bin/codex"):
                with patch("burnless.providers.codex_adapter.subprocess.run") as mock_codex_run:
                    mock_codex_run.return_value = MagicMock(
                        returncode=0,
                        stdout=json.dumps({"type": "turn.completed", "usage": {}}),
                        stderr="",
                    )
                    rc, stdout, stderr = run_ask("gold", "hello", cfg, provider="codex")

        mock_claude_run.assert_not_called()
        mock_codex_run.assert_called_once()
        cmd = mock_codex_run.call_args[0][0]
        assert "claude" not in cmd
        assert rc == 0

    def test_explicit_model_with_codex_provider_routes_to_codex_not_claude(self):
        """Bug fix #1: explicit --model must not force the claude-CLI path
        when --provider codex disambiguates the transport."""
        cfg = {"agents": {"diamond": {"provider": "codex", "command": "codex exec -m gpt-5.6-sol"}}}
        with patch("burnless.pure_ask.subprocess.run") as mock_claude_run:
            with patch("burnless.providers.codex_adapter.shutil.which", return_value="/usr/local/bin/codex"):
                with patch("burnless.providers.codex_adapter.subprocess.run") as mock_codex_run:
                    mock_codex_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    rc, stdout, stderr = run_ask(
                        "diamond", "hi", cfg, model="gpt-5-explicit-override", provider="codex"
                    )

        mock_claude_run.assert_not_called()
        cmd = mock_codex_run.call_args[0][0]
        assert "gpt-5-explicit-override" in cmd

    def test_unsupported_provider_raises_before_any_subprocess(self):
        """Bug fix #2: tiers with an unregistered provider must raise, not
        silently fall through to `claude -p`."""
        cfg = {"agents": {"silver": {"provider": "gemini", "model": "gemini-pro"}}}
        with patch("burnless.pure_ask.subprocess.run") as mock_run:
            with pytest.raises(ValueError, match="unsupported provider"):
                run_ask("silver", "hi", cfg)
        mock_run.assert_not_called()

    def test_provider_none_keeps_legacy_claude_cli_behavior(self):
        """No provider specified + no explicit model + anthropic tier: unchanged."""
        cfg = {"agents": {"silver": {"provider": "anthropic", "model": "claude-sonnet-5"}}}
        with patch("burnless.pure_ask.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            rc, stdout, stderr = run_ask("silver", "hi", cfg)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert rc == 0


class TestPrefixCache:
    """M6 wave A — `--prefix-file`/`--cache-key` core wiring (sec 14)."""

    def test_prefix_hash_computed_from_file_content(self, tmp_path):
        content = "STABLE PREFIX CONTENT\nline two"
        path = tmp_path / "prefix.txt"
        path.write_text(content, encoding="utf-8")

        cfg = {"agents": {"gold": {"provider": "anthropic", "model": "claude-opus-4-8"}}}
        request = AskRequest(prompt="hi", tier="gold", prefix_file=str(path))
        target = AnthropicAdapter().resolve(request, cfg, prefix_content=content)

        expected = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert target.prefix_hash == expected
        assert len(target.prefix_hash.split(":", 1)[1]) == 64

    def test_prefix_hash_none_when_no_prefix_file(self):
        cfg = {"agents": {"gold": {"provider": "anthropic", "model": "claude-opus-4-8"}}}
        request = AskRequest(prompt="hi", tier="gold")
        target = AnthropicAdapter().resolve(request, cfg)
        assert target.prefix_hash is None

    def test_prefix_content_appended_to_system_not_prompt(self):
        adapter = AnthropicAdapter()
        request = AskRequest(prompt="THE VARIABLE PAYLOAD", tier="gold", system="base")
        target = ResolvedAskTarget(effective_tier="gold", provider="anthropic", model="claude-opus-4-8")
        with patch("burnless.providers.anthropic_adapter.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            adapter.invoke_text(request, target, prefix_content="PREFIX TEXT")

        cmd = mock_run.call_args[0][0]
        system_idx = cmd.index("--system-prompt") + 1
        assert "base" in cmd[system_idx]
        assert "PREFIX TEXT" in cmd[system_idx]
        assert mock_run.call_args[1]["input"] == "THE VARIABLE PAYLOAD"

    def test_cache_key_recorded_not_validated(self):
        """Same --cache-key label, different prefix content: both calls succeed
        and each hash reflects that call's own current content — no gate."""
        cfg = {"agents": {"gold": {"provider": "anthropic", "model": "claude-opus-4-8"}}}
        adapter = AnthropicAdapter()

        request1 = AskRequest(prompt="hi", tier="gold", cache_key="same-label")
        target1 = adapter.resolve(request1, cfg, prefix_content="version one")

        request2 = AskRequest(prompt="hi", tier="gold", cache_key="same-label")
        target2 = adapter.resolve(request2, cfg, prefix_content="version two")

        assert target1.prefix_hash != target2.prefix_hash
        assert request1.cache_key == request2.cache_key == "same-label"

    def test_explain_reports_supported_for_anthropic_with_prefix(self):
        target = ResolvedAskTarget(
            effective_tier="gold", provider="anthropic", model="claude-opus-4-8",
            prefix_hash="sha256:" + "a" * 64,
        )
        target = dataclasses_replace_caps(target, prefix_cache=True)
        explain = AnthropicAdapter().explain(target)
        assert explain["prefix_cache_status"] == "supported"

    def test_explain_reports_unsupported_for_codex_with_prefix(self):
        target = ResolvedAskTarget(
            effective_tier="diamond", provider="codex", model="gpt-5.6-sol",
            prefix_hash="sha256:" + "a" * 64,
        )
        target = dataclasses_replace_caps(target, prefix_cache=False)
        explain = CodexAdapter().explain(target)
        assert explain["prefix_cache_status"] == "unsupported"

    def test_explain_omits_status_when_no_prefix_used(self):
        target = ResolvedAskTarget(effective_tier="gold", provider="anthropic", model="claude-opus-4-8")
        explain = AnthropicAdapter().explain(target)
        assert "prefix_cache_status" not in explain or explain.get("prefix_cache_status") is None

    def test_codex_receives_prefix_content_functionally(self):
        adapter = CodexAdapter()
        request = AskRequest(prompt="hi", tier="diamond")
        target = ResolvedAskTarget(effective_tier="diamond", provider="codex", model="gpt-5.6-sol", auth="subscription")
        with patch("burnless.providers.codex_adapter.shutil.which", return_value="/usr/local/bin/codex"):
            with patch("burnless.providers.codex_adapter.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                adapter.invoke_text(request, target, prefix_content="CODEX PREFIX MARKER")
        cmd = mock_run.call_args[0][0]
        assert any("CODEX PREFIX MARKER" in part for part in cmd)

    def test_ollama_receives_prefix_content_functionally(self):
        adapter = OllamaAdapter()
        request = AskRequest(prompt="hi", tier="bronze")
        target = ResolvedAskTarget(effective_tier="bronze", provider="ollama", model="gemma-4-eb")
        with patch(
            "burnless.providers.ollama_adapter.pure_ask.run_ask_ollama",
            return_value=(0, "hello", ""),
        ) as mock_ollama:
            adapter.invoke_text(request, target, prefix_content="OLLAMA PREFIX MARKER")
        assert "OLLAMA PREFIX MARKER" in mock_ollama.call_args[1]["system"]


def dataclasses_replace_caps(target, *, prefix_cache: bool):
    import dataclasses
    return dataclasses.replace(
        target, capabilities=dataclasses.replace(target.capabilities, prefix_cache=prefix_cache)
    )
