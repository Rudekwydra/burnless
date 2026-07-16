from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .core import ContextUsage
from .cadence import decide_compaction, CadenceConfig, CompactDecision


@dataclass
class CadenceController:
    usage_provider: Callable[[], ContextUsage]
    idle_provider: Callable[[], bool]
    backlog_provider: Callable[[], int]
    focus_provider: Callable[[], str]
    cfg: CadenceConfig = field(default_factory=CadenceConfig)
    poll_interval_s: float = 3.0
    cooldown_s: float = 30.0
    _last_poll: float | None = field(default=None, init=False)
    _last_compact: float | None = field(default=None, init=False)
    _last_decision: CompactDecision | None = field(default=None, init=False)

    def tick(self, now: float) -> bytes | None:
        # Route C injector: rate-limit poll, apply hysteresis, gather defensively, decide, emit /compact.
        if self._last_poll is not None and (now - self._last_poll) < self.poll_interval_s:
            return None

        self._last_poll = now

        if self._last_compact is not None and (now - self._last_compact) < self.cooldown_s:
            return None

        try:
            usage = self.usage_provider()
        except Exception:
            usage = ContextUsage(current=None, limit=None)

        try:
            is_idle = bool(self.idle_provider())
        except Exception:
            is_idle = False

        try:
            backlog = int(self.backlog_provider())
        except Exception:
            backlog = 0

        decision = decide_compaction(usage, is_idle, backlog, self.cfg)
        self._last_decision = decision

        if not decision.should:
            return None

        self._last_compact = now

        try:
            focus = self.focus_provider()
        except Exception:
            focus = ""

        focus = (focus or "").strip()

        if focus:
            cmd = "/compact focus on " + focus
        else:
            cmd = "/compact"

        return (cmd + "\r").encode()


def build_injector(controller: CadenceController, clock: Callable[[], float]) -> Callable[[], bytes | None]:
    return lambda: controller.tick(clock())
