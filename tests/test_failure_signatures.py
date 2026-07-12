import pytest
from burnless.live_runner import is_context_overflow_text
from burnless.agents import _retryable_provider_failure


class TestContextOverflowSignatures:
    """Test that is_context_overflow_text correctly distinguishes error contexts from code mention."""

    def test_max_tokens_in_code_prosa_not_overflow(self):
        """max_tokens mentioned in API documentation should NOT trigger overflow."""
        text = (
            "The API accepts a max_tokens parameter to control output length. "
            "Set max_tokens=2048 for longer completions."
        )
        assert not is_context_overflow_text(text)

    def test_max_tokens_exceeded_is_overflow(self):
        """Explicit error message 'max_tokens exceeded' should trigger overflow."""
        text = "Error: max_tokens exceeded, reduce prompt length"
        assert is_context_overflow_text(text)

    def test_exceeds_max_tokens_is_overflow(self):
        """Pattern 'exceeds max_tokens' should trigger overflow."""
        text = "Your request exceeds max_tokens limit"
        assert is_context_overflow_text(text)

    def test_max_tokens_limit_is_overflow(self):
        """Pattern 'max_tokens limit' should trigger overflow."""
        text = "Prompt size hit max_tokens limit"
        assert is_context_overflow_text(text)

    def test_maximum_context_length_is_overflow(self):
        """Pattern 'maximum context length' should trigger overflow."""
        text = "Error: maximum context length exceeded"
        assert is_context_overflow_text(text)

    def test_prompt_too_long_is_overflow(self):
        """Original pattern 'prompt is too long' should still trigger overflow."""
        text = "Your prompt is too long for this model"
        assert is_context_overflow_text(text)

    def test_context_length_exceeded_is_overflow(self):
        """Original pattern 'context_length_exceeded' should still trigger overflow."""
        text = "Error: context_length_exceeded"
        assert is_context_overflow_text(text)

    def test_empty_text_not_overflow(self):
        """Empty text should not trigger overflow."""
        assert not is_context_overflow_text("")
        assert not is_context_overflow_text(None)


class TestRetryableProviderSignatures:
    """Test that _retryable_provider_failure correctly identifies HTTP errors vs benign mentions."""

    def test_numeric_code_without_context_not_retryable(self):
        """Bare number like '512' in stdout should NOT be retryable."""
        result = {
            "returncode": 1,
            "stdout": "512 arquivos processados",
            "stderr": "",
            "timed_out": False,
            "stale": False,
        }
        assert not _retryable_provider_failure(result)

    def test_delegation_id_number_not_retryable(self):
        """Result mentioning 'd523' should NOT be retryable."""
        result = {
            "returncode": 1,
            "stdout": "d523 concluído",
            "stderr": "",
            "timed_out": False,
            "stale": False,
        }
        assert not _retryable_provider_failure(result)

    def test_http_5xx_is_retryable(self):
        """HTTP error code (e.g., 'HTTP 529') should be retryable."""
        result = {
            "returncode": 1,
            "stdout": "",
            "stderr": "HTTP 529 overloaded",
            "timed_out": False,
            "stale": False,
        }
        assert _retryable_provider_failure(result)

    def test_status_code_5xx_is_retryable(self):
        """status_code with 5xx (e.g., 'status_code 500') should be retryable."""
        result = {
            "returncode": 1,
            "stdout": "API request failed: status_code 502",
            "stderr": "",
            "timed_out": False,
            "stale": False,
        }
        assert _retryable_provider_failure(result)

    def test_error_5xx_is_retryable(self):
        """Error message with 5xx code should be retryable."""
        result = {
            "returncode": 1,
            "stdout": "",
            "stderr": "error 500 Internal Server Error",
            "timed_out": False,
            "stale": False,
        }
        assert _retryable_provider_failure(result)

    def test_5xx_followed_by_server_error_is_retryable(self):
        """5xx code followed by Server Error should be retryable."""
        result = {
            "returncode": 1,
            "stdout": "",
            "stderr": "Provider returned 503 Service Unavailable",
            "timed_out": False,
            "stale": False,
        }
        assert _retryable_provider_failure(result)

    def test_5xx_followed_by_gateway_timeout_is_retryable(self):
        """5xx code followed by Gateway Timeout should be retryable."""
        result = {
            "returncode": 1,
            "stdout": "",
            "stderr": "504 Gateway Timeout",
            "timed_out": False,
            "stale": False,
        }
        assert _retryable_provider_failure(result)

    def test_timeout_is_retryable(self):
        """'timeout' keyword should be retryable."""
        result = {
            "returncode": 124,
            "stdout": "Command timeout",
            "stderr": "",
            "timed_out": False,
            "stale": False,
        }
        assert _retryable_provider_failure(result)

    def test_timed_out_is_retryable(self):
        """'timed out' keyword should be retryable."""
        result = {
            "returncode": 124,
            "stdout": "",
            "stderr": "Agent process timed out",
            "timed_out": False,
            "stale": False,
        }
        assert _retryable_provider_failure(result)

    def test_timed_out_flag_is_retryable(self):
        """timed_out=True flag should be retryable."""
        result = {
            "returncode": 124,
            "stdout": "",
            "stderr": "",
            "timed_out": True,
            "stale": False,
        }
        assert _retryable_provider_failure(result)

    def test_stale_flag_is_retryable(self):
        """stale=True flag should be retryable."""
        result = {
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "stale": True,
        }
        assert _retryable_provider_failure(result)

    def test_no_error_not_retryable(self):
        """Result with no error markers should not be retryable."""
        result = {
            "returncode": 0,
            "stdout": "Task completed successfully",
            "stderr": "",
            "timed_out": False,
            "stale": False,
        }
        assert not _retryable_provider_failure(result)
