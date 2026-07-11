from __future__ import annotations

import os
import tempfile
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from burnless.pure_ask import (
    resolve_ask_model,
    build_ask_command,
    run_ask,
    _DISALLOWED_TOOLS,
)


class TestBuildAskCommand:
    """Test build_ask_command output structure and content."""

    def test_contains_disallowed_tools(self):
        cmd = build_ask_command("claude-opus-4-8")
        assert "--disallowedTools" in cmd
        idx = cmd.index("--disallowedTools")
        # Should be followed by the list of disallowed tools
        tools_in_cmd = cmd[idx + 1 : idx + 1 + len(_DISALLOWED_TOOLS)]
        assert tools_in_cmd == _DISALLOWED_TOOLS

    def test_contains_exclude_dynamic_system_prompt_sections(self):
        cmd = build_ask_command("claude-opus-4-8")
        assert "--exclude-dynamic-system-prompt-sections" in cmd

    def test_contains_system_prompt(self):
        cmd = build_ask_command("claude-opus-4-8")
        assert "--system-prompt" in cmd

    def test_no_permission_mode(self):
        cmd = build_ask_command("claude-opus-4-8")
        assert "--permission-mode" not in cmd

    def test_no_allowed_tools(self):
        cmd = build_ask_command("claude-opus-4-8")
        assert "--allowedTools" not in cmd

    def test_no_bypass_permissions(self):
        cmd = build_ask_command("claude-opus-4-8")
        cmd_str = " ".join(cmd)
        assert "bypassPermissions" not in cmd_str

    def test_custom_system_prompt(self):
        custom = "You are a test."
        cmd = build_ask_command("claude-opus-4-8", system=custom)
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == custom

    def test_max_budget_usd_omitted_by_default(self):
        cmd = build_ask_command("claude-opus-4-8")
        assert "--max-budget-usd" not in cmd

    def test_max_budget_usd_passthrough(self):
        cmd = build_ask_command("claude-opus-4-8", max_budget_usd=0.50)
        idx = cmd.index("--max-budget-usd")
        assert cmd[idx + 1] == "0.5"


class TestResolveAskModel:
    """Test model resolution from tier config."""

    def test_resolve_from_model_field(self):
        cfg = {
            "agents": {
                "gold": {
                    "provider": "anthropic",
                    "model": "claude-opus-4-8",
                    "command": "claude --model something-else",
                }
            }
        }
        model = resolve_ask_model("gold", cfg)
        assert model == "claude-opus-4-8"

    def test_resolve_from_command_field(self):
        cfg = {
            "agents": {
                "silver": {
                    "provider": "anthropic",
                    "command": "claude --model claude-sonnet-5",
                }
            }
        }
        model = resolve_ask_model("silver", cfg)
        assert model == "claude-sonnet-5"

    def test_raises_on_ollama_local(self):
        cfg = {
            "agents": {
                "bronze": {
                    "provider": "ollama-local",
                    "model": "gemma-4-eb",
                }
            }
        }
        with pytest.raises(ValueError, match="ollama-local"):
            resolve_ask_model("bronze", cfg)

    def test_raises_on_ollama(self):
        cfg = {
            "agents": {
                "bronze": {
                    "provider": "ollama",
                    "model": "gemma-4-eb",
                }
            }
        }
        with pytest.raises(ValueError, match="ollama"):
            resolve_ask_model("bronze", cfg)

    def test_raises_when_no_model_found(self):
        cfg = {
            "agents": {
                "silver": {
                    "provider": "anthropic",
                    "command": "claude",
                }
            }
        }
        with pytest.raises(ValueError, match="could not resolve"):
            resolve_ask_model("silver", cfg)


class TestRunAsk:
    """Test run_ask subprocess behavior."""

    def test_cwd_is_temp_dir(self):
        cfg = {
            "agents": {
                "silver": {
                    "provider": "anthropic",
                    "model": "claude-sonnet-5",
                }
            }
        }
        prompt = "hello"

        with patch("burnless.pure_ask.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="response",
                stderr=""
            )
            run_ask("silver", prompt, cfg)

            # Check that subprocess.run was called with cwd != current directory
            call_kwargs = mock_run.call_args[1]
            cwd_arg = call_kwargs.get("cwd")
            assert cwd_arg is not None
            assert cwd_arg == tempfile.gettempdir()
            assert cwd_arg != os.getcwd()

    def test_returns_returncode_stdout_stderr(self):
        cfg = {
            "agents": {
                "silver": {
                    "provider": "anthropic",
                    "model": "claude-sonnet-5",
                }
            }
        }
        prompt = "hello"

        with patch("burnless.pure_ask.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=42,
                stdout="my output",
                stderr="my error"
            )
            rc, stdout, stderr = run_ask("silver", prompt, cfg)

            assert rc == 42
            assert stdout == "my output"
            assert stderr == "my error"

    def test_explicit_model_bypasses_resolve_ask_model(self):
        """When model is passed explicitly, use it without calling resolve_ask_model."""
        cfg = {
            "agents": {
                "silver": {
                    "provider": "ollama-local",
                    "model": "gemma-4-eb",
                }
            }
        }
        prompt = "hello"

        with patch("burnless.pure_ask.subprocess.run") as mock_run:
            with patch("burnless.pure_ask.resolve_ask_model") as mock_resolve:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="response",
                    stderr=""
                )
                # Pass explicit model; resolve_ask_model should NOT be called
                rc, stdout, stderr = run_ask("silver", prompt, cfg, model="claude-opus-4-8")

                # Verify resolve_ask_model was NOT called (because model was explicitly provided)
                mock_resolve.assert_not_called()
                # Verify subprocess.run was called with the explicit model
                call_args = mock_run.call_args[0][0]
                assert "--model" in call_args
                model_idx = call_args.index("--model")
                assert call_args[model_idx + 1] == "claude-opus-4-8"

    def test_no_model_uses_resolve_ask_model(self):
        """When model is None, call resolve_ask_model normally."""
        cfg = {
            "agents": {
                "silver": {
                    "provider": "anthropic",
                    "model": "claude-sonnet-5",
                }
            }
        }
        prompt = "hello"

        with patch("burnless.pure_ask.subprocess.run") as mock_run:
            with patch("burnless.pure_ask.resolve_ask_model", return_value="claude-sonnet-5") as mock_resolve:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="response",
                    stderr=""
                )
                # Pass model=None (or omit it)
                rc, stdout, stderr = run_ask("silver", prompt, cfg, model=None)

                # Verify resolve_ask_model WAS called
                mock_resolve.assert_called_once_with("silver", cfg)
                # Verify subprocess.run was called with the resolved model
                call_args = mock_run.call_args[0][0]
                assert "--model" in call_args
                model_idx = call_args.index("--model")
                assert call_args[model_idx + 1] == "claude-sonnet-5"
