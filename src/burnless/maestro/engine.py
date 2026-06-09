"""Unified maestro engine prototype: partner loop + rolling rewind-recompact.

ADDITIVE prototype (M1 of v1) — not wired into any command yet. See
_design/TARGET_ARCHITECTURE_2026-06-09.md §0.A / §0.A.1.

The maestro is a tool-less partner. Context per turn =
[rolling capsule] + [window of recent turns] + [user msg]. When the window
crosses should_compact()'s ROI threshold, it is ultra-compacted into a new
rolling capsule (persisted to disk) and the window resets ("rewind").
Long-term memory = the chain of capsules on disk; working memory = window.

Model and compaction calls are injected as callables so the engine is fully
unit-testable offline. The real implementations wire ModelFn to the claude
warm-fork machinery (warm_session.fork_args) and CompactFn to a bronze
compaction call; this module never touches LLM/network/subprocess itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .. import cache_policy


@dataclass
class Turn:
    role: str            # "user" | "maestro"
    text: str
    tokens: int          # estimated token count of this turn


@dataclass
class PartnerState:
    rolling_capsule: str = ""          # compact carry-forward state (the "prompt 1" of each cycle)
    window: list[Turn] = field(default_factory=list)
    cycle: int = 0                     # how many compaction cycles have happened
    capsule_paths: list[str] = field(default_factory=list)


# Injectable side-effects (real impls wire to claude warm-fork / bronze compaction;
# tests inject deterministic fakes so no LLM/network is needed).
ModelFn = Callable[[str], tuple[str, int]]   # (assembled_prompt) -> (response_text, response_tokens)
CompactFn = Callable[[str], str]             # (prior capsule + window blob) -> new compact capsule text


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def assemble_prompt(state: PartnerState, user_text: str) -> str:
    """Bounded context for one model call: capsule + window + current user msg."""
    parts = []
    if state.rolling_capsule:
        parts.append("## State\n" + state.rolling_capsule)
    for t in state.window:
        parts.append(f"{t.role}: {t.text}")
    parts.append(f"user: {user_text}")
    return "\n\n".join(parts)


def window_tokens(state: PartnerState) -> int:
    return sum(t.tokens for t in state.window)


def maybe_compact(
    state: PartnerState,
    cfg: dict,
    compact_fn: CompactFn,
    burnless_root: Optional[Path] = None,
) -> bool:
    """If should_compact says it pays, ultra-compact the window into a new
    rolling capsule and REWIND (clear the window). Returns True if compacted."""
    cp = (cfg.get("cache_policy") or {})
    capsule_budget = int(cp.get("capsule_budget_tokens", 1500))
    decision = cache_policy.should_compact(
        old_tokens=window_tokens(state),
        compacted_tokens=capsule_budget,
        expected_future_turns=int(cp.get("expected_future_turns", 8)),
        cache_read_ratio=float(cp.get("cache_read_ratio", 0.10)),
        cache_write_ratio=float(cp.get("cache_write_ratio", 2.0)),
        compaction_cost_tokens=int(cp.get("compaction_cost_tokens", 4000)),
        min_hot_tail_tokens=int(cp.get("min_hot_tail_tokens", 1500)),
    )
    if not decision.should_compact:
        return False
    blob = (state.rolling_capsule + "\n\n" if state.rolling_capsule else "") + \
        "\n".join(f"{t.role}: {t.text}" for t in state.window)
    state.rolling_capsule = compact_fn(blob)
    state.window = []                      # REWIND
    state.cycle += 1
    if burnless_root is not None:
        d = Path(burnless_root) / "maestro" / "rolling"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"capsule_{state.cycle}.json"
        p.write_text(
            json.dumps(
                {"cycle": state.cycle, "capsule": state.rolling_capsule},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        state.capsule_paths.append(str(p))
    return True


def partner_turn(
    state: PartnerState,
    user_text: str,
    *,
    cfg: dict,
    model_fn: ModelFn,
    compact_fn: CompactFn,
    burnless_root: Optional[Path] = None,
) -> str:
    """One partner turn: assemble bounded context -> model -> append -> maybe rolling-compact.

    assemble_prompt() supplies the current user msg itself, so the model sees
    state + window + current-user exactly once; the user Turn joins the window
    only after the prompt is assembled.
    """
    prompt = assemble_prompt(state, user_text)
    response, rtoks = model_fn(prompt)
    state.window.append(Turn("user", user_text, estimate_tokens(user_text)))
    state.window.append(Turn("maestro", response, rtoks))
    maybe_compact(state, cfg, compact_fn, burnless_root)
    return response
