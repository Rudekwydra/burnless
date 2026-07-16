from __future__ import annotations

from dataclasses import dataclass

from .core import ContextUsage


@dataclass(frozen=True)
class CadenceConfig:
    min_backlog_turns: int = 4
    soft_ceiling_ratio: float = 0.70
    hard_ceiling_ratio: float = 0.88
    backlog_forces_turns: int = 12


@dataclass(frozen=True)
class CompactDecision:
    should: bool
    reason: str
    urgency: str


def decide_compaction(
    usage: ContextUsage,
    is_idle: bool,
    backlog_turns: int,
    cfg: CadenceConfig | None = None,
) -> CompactDecision:
    # Auto-equilibrium piso/teto: hard ceiling (forced), soft ceiling (idle latency window), backlog floor (/compact min-content).
    cfg = cfg or CadenceConfig()

    ratio = None
    if usage.current is not None and usage.limit is not None and usage.limit > 0:
        ratio = usage.current / usage.limit

    # Rule 1: backlog below min-content floor
    if backlog_turns < cfg.min_backlog_turns:
        return CompactDecision(False, "backlog below min-content floor", "none")

    # Rule 2: hard ceiling (forced compact)
    if ratio is not None and ratio >= cfg.hard_ceiling_ratio:
        return CompactDecision(
            True,
            f"usage {ratio:.2f} >= hard ceiling {cfg.hard_ceiling_ratio}",
            "forced",
        )

    # Rule 3: soft ceiling + idle (latency-hiding window)
    if is_idle and ratio is not None and ratio >= cfg.soft_ceiling_ratio:
        return CompactDecision(
            True,
            f"idle and usage {ratio:.2f} >= soft ceiling {cfg.soft_ceiling_ratio}",
            "idle",
        )

    # Rule 4: usage unknown, idle, large backlog
    if ratio is None and is_idle and backlog_turns >= cfg.backlog_forces_turns:
        return CompactDecision(True, "usage unknown; idle with large backlog", "idle")

    # Rule 5: no trigger
    return CompactDecision(False, "no trigger", "none")
