"""Tests for ollama_worker guards (reachability + size checks)."""
import os
import pytest
from unittest.mock import patch, MagicMock

from burnless.ollama_worker import _is_local_reachable, run_ollama_tools


class TestIsLocalReachable:
    """Tests for _is_local_reachable probe."""

    def test_ollama_unreachable_port(self):
        """Ollama mode: port closed should return False."""
        result = _is_local_reachable("ollama", "http://localhost:19999", timeout=0.5)
        assert result is False

    def test_llamacpp_unreachable_port(self):
        """Llamacpp mode: port closed should return False."""
        result = _is_local_reachable("llamacpp", "http://localhost:19999", timeout=0.5)
        assert result is False

    def test_ollama_timeout(self):
        """Ollama mode: timeout should return False (network error)."""
        # localhost:1 is typically unreachable or times out
        result = _is_local_reachable("ollama", "http://localhost:1", timeout=0.5)
        assert result is False


class TestRunOllamaToolsGuards:
    """Tests for run_ollama_tools early-exit guards."""

    def test_prompt_size_exceeded(self):
        """Prompt >6KB (default) should return ERR without attempting network."""
        large_prompt = "X" * 8000  # ~8KB
        result = run_ollama_tools("test-model", large_prompt, cwd="/tmp")
        assert result["status"] == "ERR"
        assert "KB" in result["summary"] or "kb" in result["summary"].lower()
        assert result["files_touched"] == []
        assert len(result["issues"]) == 1

    def test_prompt_size_override_env(self):
        """BURNLESS_BRONZE_LOCAL_MAX_SPEC_KB env override should enforce new limit."""
        with patch.dict(os.environ, {"BURNLESS_BRONZE_LOCAL_MAX_SPEC_KB": "1"}):
            prompt_2kb = "Y" * 2000
            result = run_ollama_tools("test-model", prompt_2kb, cwd="/tmp")
            assert result["status"] == "ERR"
            assert "KB" in result["summary"] or "kb" in result["summary"].lower()
            assert result["files_touched"] == []

    def test_server_unreachable(self):
        """Server unreachable should return ERR without attempting request."""
        small_prompt = "short prompt"
        with patch.dict(os.environ, {"BURNLESS_LOCAL_API": "ollama"}):
            result = run_ollama_tools(
                "test-model", small_prompt, cwd="/tmp", host="http://localhost:19999"
            )
            assert result["status"] == "ERR"
            assert "inalcancavel" in result["summary"]
            assert result["files_touched"] == []

    def test_server_unreachable_llamacpp_mode(self):
        """Llamacpp mode with unreachable server should return ERR."""
        small_prompt = "short prompt"
        with patch.dict(
            os.environ,
            {
                "BURNLESS_LOCAL_API": "llamacpp",
                "BURNLESS_LOCAL_HOST": "http://localhost:19999",
            },
        ):
            result = run_ollama_tools("test-model", small_prompt, cwd="/tmp")
            assert result["status"] == "ERR"
            assert "inalcancavel" in result["summary"]
            assert result["files_touched"] == []

    def test_both_guards_size_then_reachability(self):
        """Size guard runs first; if it fails, reachability never checked."""
        large_prompt = "Z" * 8000
        with patch.dict(
            os.environ, {"BURNLESS_LOCAL_API": "ollama"}
        ):
            result = run_ollama_tools(
                "test-model", large_prompt, cwd="/tmp", host="http://localhost:19999"
            )
            assert result["status"] == "ERR"
            # Should fail on size, not reachability
            assert "KB" in result["summary"] or "kb" in result["summary"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
