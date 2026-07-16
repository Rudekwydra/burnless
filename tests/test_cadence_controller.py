from __future__ import annotations

from burnless.pilot.core import ContextUsage
from burnless.pilot.cadence import CadenceConfig
from burnless.pilot.cadence_controller import CadenceController, build_injector


def mk(usage, idle, backlog, focus="ep1", **kw):
    return CadenceController(
        usage_provider=lambda: usage,
        idle_provider=lambda: idle,
        backlog_provider=lambda: backlog,
        focus_provider=lambda: focus,
        **kw,
    )


def test_compact_fires_on_hard_ceiling():
    controller = mk(ContextUsage(180000, 200000), False, 8)
    result = controller.tick(0.0)
    assert result == b"/compact focus on ep1\r"
    assert controller._last_compact == 0.0


def test_rate_limit_skips_providers():
    call_count = [0]

    def counting_backlog():
        call_count[0] += 1
        return 8

    controller = CadenceController(
        usage_provider=lambda: ContextUsage(50000, 200000),
        idle_provider=lambda: False,
        backlog_provider=counting_backlog,
        focus_provider=lambda: "ep1",
        poll_interval_s=3.0,
    )

    controller.tick(0.0)
    initial_count = call_count[0]

    controller.tick(1.0)
    assert call_count[0] == initial_count, "backlog_provider should not be called within rate-limit window"


def test_cooldown_blocks_recompact():
    controller = mk(ContextUsage(180000, 200000), False, 8, cooldown_s=30.0)
    first = controller.tick(0.0)
    assert first == b"/compact focus on ep1\r"

    second = controller.tick(5.0)
    assert second is None


def test_recompact_after_cooldown():
    controller = mk(ContextUsage(180000, 200000), False, 8, cooldown_s=30.0)
    first = controller.tick(0.0)
    assert first == b"/compact focus on ep1\r"

    second = controller.tick(40.0)
    assert second == b"/compact focus on ep1\r"
    assert controller._last_compact == 40.0


def test_no_trigger_returns_none():
    controller = mk(ContextUsage(50000, 200000), True, 8)
    result = controller.tick(0.0)
    assert result is None


def test_empty_focus_uses_bare_compact():
    controller = mk(ContextUsage(180000, 200000), False, 8, focus="")
    result = controller.tick(0.0)
    assert result == b"/compact\r"


def test_provider_exceptions_are_swallowed():
    def raising_usage():
        raise RuntimeError("provider error")

    def raising_idle():
        raise RuntimeError("provider error")

    def raising_backlog():
        raise RuntimeError("provider error")

    controller = CadenceController(
        usage_provider=raising_usage,
        idle_provider=raising_idle,
        backlog_provider=raising_backlog,
        focus_provider=lambda: "ep1",
    )

    result = controller.tick(0.0)
    assert result is None


def test_build_injector_uses_clock():
    t = [0.0]

    def mutable_clock():
        return t[0]

    controller = mk(ContextUsage(180000, 200000), False, 8, poll_interval_s=3.0)
    inj = build_injector(controller, mutable_clock)

    first = inj()
    assert first == b"/compact focus on ep1\r"

    t[0] = 1.0
    second = inj()
    assert second is None
