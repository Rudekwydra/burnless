from __future__ import annotations

import json
import os
import tempfile
import subprocess
from unittest.mock import patch, MagicMock, ANY

import pytest

from burnless import estimator
from burnless.pure_ask import (
    resolve_ask_model,
    resolve_ask_provider,
    build_ask_command,
    compute_budget_plan,
    run_ask,
    run_ask_ollama,
    _DISALLOWED_TOOLS,
)
from burnless.providers.contracts import AskRequest, ProviderCapabilities


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

    def test_effort_omitted_by_default(self):
        cmd = build_ask_command("claude-opus-4-8")
        assert "--effort" not in cmd

    def test_effort_passthrough(self):
        cmd = build_ask_command("claude-opus-4-8", effort="high")
        idx = cmd.index("--effort")
        assert cmd[idx + 1] == "high"


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


class TestAskOllamaRouting:
    """Test ollama/ollama-local routing in run_ask."""

    def test_ollama_local_routing_bypasses_subprocess(self):
        """When provider is ollama-local and no explicit model, route to run_ask_ollama."""
        cfg = {
            "agents": {
                "bronze": {
                    "provider": "ollama-local",
                    "model": "gemma-4-eb",
                }
            }
        }
        prompt = "hi"

        mock_response = MagicMock()
        mock_response.__enter__ = lambda self: self
        mock_response.__exit__ = lambda self, *args: None
        mock_response.read.return_value = json.dumps({
            "message": {"content": "GRIFO-77 / haiku"}
        }).encode()

        with patch.dict(os.environ, {"BURNLESS_LOCAL_API": "ollama"}):
            with patch("burnless.pure_ask.urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
                with patch("burnless.pure_ask.subprocess.run") as mock_subprocess:
                    rc, stdout, stderr = run_ask("bronze", prompt, cfg)

                    # Should NOT call subprocess.run
                    mock_subprocess.assert_not_called()

                    # Should call urlopen
                    assert mock_urlopen.called
                    rc_val, stdout_val, stderr_val = rc, stdout, stderr
                    assert rc_val == 0
                    assert stdout_val == "GRIFO-77 / haiku"
                    assert stderr_val == ""

    def test_ollama_local_payload_contains_think_false(self):
        """Verify the JSON payload contains 'think': False."""
        cfg = {
            "agents": {
                "bronze": {
                    "provider": "ollama-local",
                    "model": "gemma-4-eb",
                }
            }
        }
        prompt = "hi"

        mock_response = MagicMock()
        mock_response.__enter__ = lambda self: self
        mock_response.__exit__ = lambda self, *args: None
        mock_response.read.return_value = json.dumps({
            "message": {"content": "test"}
        }).encode()

        with patch.dict(os.environ, {"BURNLESS_LOCAL_API": "ollama"}):
            with patch("burnless.pure_ask.urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
                with patch("burnless.pure_ask.subprocess.run"):
                    run_ask("bronze", prompt, cfg)

                    # Inspect the Request object passed to urlopen
                    call_args = mock_urlopen.call_args
                    request_obj = call_args[0][0]
                    payload_data = request_obj.data
                    payload = json.loads(payload_data.decode("utf-8"))

                    # Check that 'think' is False
                    assert "think" in payload
                    assert payload["think"] is False

    def test_resolve_ask_provider(self):
        """Test resolve_ask_provider returns correct provider."""
        cfg = {
            "agents": {
                "bronze": {"provider": "ollama-local", "model": "gemma-4-eb"},
                "gold": {"provider": "anthropic", "model": "claude-opus-4-8"},
            }
        }
        assert resolve_ask_provider("bronze", cfg) == "ollama-local"
        assert resolve_ask_provider("gold", cfg) == "anthropic"

    def test_resolve_ask_provider_defaults_to_anthropic(self):
        """Test resolve_ask_provider defaults to anthropic."""
        cfg = {
            "agents": {
                "silver": {"model": "claude-sonnet-5"},
            }
        }
        assert resolve_ask_provider("silver", cfg) == "anthropic"

    def test_ollama_model_resolution_error(self):
        """When ollama provider has no model, raise ValueError."""
        cfg = {
            "agents": {
                "bronze": {"provider": "ollama-local"},
            }
        }
        prompt = "hi"

        with pytest.raises(ValueError, match="could not resolve a local model"):
            run_ask("bronze", prompt, cfg)


class TestComputeBudgetPlan:
    """Test compute_budget_plan preflight estimation + enforcement decision."""

    def test_no_budget_flags_soft_only_and_estimate_matches(self):
        request = AskRequest(prompt="hello world" * 10, tier="silver")
        caps = ProviderCapabilities()
        plan = compute_budget_plan(request, "claude-sonnet-5", caps)
        assert plan.enforcement == "soft_only"
        assert plan.estimated_input_tokens == estimator.estimate_tokens(request.prompt)

    def test_hard_policy_with_spend_cap_and_budget_usd_is_hard(self):
        request = AskRequest(
            prompt="hello",
            tier="silver",
            budget_policy="hard",
            max_budget_usd=1.0,
        )
        caps = ProviderCapabilities(hard_spend_cap=True)
        plan = compute_budget_plan(request, "claude-sonnet-5", caps)
        assert plan.enforcement == "hard"

    def test_hard_policy_without_capability_stays_soft_only(self):
        request = AskRequest(
            prompt="hello",
            tier="silver",
            budget_policy="hard",
            max_budget_usd=1.0,
        )
        caps = ProviderCapabilities(hard_spend_cap=False)
        plan = compute_budget_plan(request, "claude-sonnet-5", caps)
        assert plan.enforcement == "soft_only"

    def test_hard_policy_with_only_max_output_tokens_stays_soft_only(self):
        request = AskRequest(
            prompt="hello",
            tier="silver",
            budget_policy="hard",
            max_output_tokens=100,
        )
        caps = ProviderCapabilities(hard_spend_cap=True)
        plan = compute_budget_plan(request, "claude-sonnet-5", caps)
        assert plan.enforcement == "soft_only"
