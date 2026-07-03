from types import SimpleNamespace
import pytest
from burnless.cli import _pilot_resolve_auto_rollover


class TestPilotResolveAutoRollover:
    """Test _pilot_resolve_auto_rollover logic."""

    def test_auto_on_by_default_with_full_capabilities(self):
        """config=None, no flags, full capabilities => auto ON by default."""
        adapter = SimpleNamespace(
            capabilities=lambda: SimpleNamespace(supports_hooks=True, supports_usage=True)
        )
        auto_rollover, diagnostic = _pilot_resolve_auto_rollover(
            cli_auto_rollover=False,
            cli_no_auto=False,
            config_auto_rollover=None,
            adapter=adapter,
            host_name="claude",
        )
        assert auto_rollover is True
        assert diagnostic is None

    def test_no_auto_flag_disables_without_diagnostic(self):
        """--no-auto flag disables auto-rollover without diagnostic (explicit user choice)."""
        adapter = SimpleNamespace(
            capabilities=lambda: SimpleNamespace(supports_hooks=True, supports_usage=True)
        )
        auto_rollover, diagnostic = _pilot_resolve_auto_rollover(
            cli_auto_rollover=False,
            cli_no_auto=True,
            config_auto_rollover=None,
            adapter=adapter,
            host_name="claude",
        )
        assert auto_rollover is False
        assert diagnostic is None

    def test_auto_disabled_when_capability_missing(self):
        """Missing supports_usage capability => auto stays OFF with diagnostic."""
        adapter = SimpleNamespace(
            capabilities=lambda: SimpleNamespace(supports_hooks=True, supports_usage=False)
        )
        auto_rollover, diagnostic = _pilot_resolve_auto_rollover(
            cli_auto_rollover=False,
            cli_no_auto=False,
            config_auto_rollover=None,
            adapter=adapter,
            host_name="codex",
        )
        assert auto_rollover is False
        assert diagnostic is not None
        assert "auto-rollover desarmado" in diagnostic

    def test_config_false_respected_with_full_capabilities(self):
        """config auto_rollover=False => auto OFF (explicit config choice, no diagnostic)."""
        adapter = SimpleNamespace(
            capabilities=lambda: SimpleNamespace(supports_hooks=True, supports_usage=True)
        )
        auto_rollover, diagnostic = _pilot_resolve_auto_rollover(
            cli_auto_rollover=False,
            cli_no_auto=False,
            config_auto_rollover=False,
            adapter=adapter,
            host_name="claude",
        )
        assert auto_rollover is False
        assert diagnostic is None

    def test_adapter_without_capabilities_method_assumes_capable(self):
        """If adapter has no capabilities() method, assume it's capable (for test/mock compat)."""
        adapter = SimpleNamespace()
        auto_rollover, diagnostic = _pilot_resolve_auto_rollover(
            cli_auto_rollover=False,
            cli_no_auto=False,
            config_auto_rollover=None,
            adapter=adapter,
            host_name="test-adapter",
        )
        assert auto_rollover is True
        assert diagnostic is None
