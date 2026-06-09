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
from .session_runner import MaestroSession


@dataclass
class Turn:
    role: str            # "user" | "maestro"
    text: str
    tokens: int          # estimated token count of this turn


@dataclass
class RollingCapsule:
    decisions: list[str] = field(default_factory=list)     # carried VERBATIM, append-only across cycles
    constraints: list[str] = field(default_factory=list)   # carried VERBATIM, append-only across cycles
    open_threads: list[str] = field(default_factory=list)  # may be rewritten each cycle
    summary: str = ""                                       # lossy chatter summary, rewritten each cycle

    def render(self) -> str:
        parts = []
        if self.decisions:    parts.append("Decisions:\n" + "\n".join(f"- {d}" for d in self.decisions))
        if self.constraints:  parts.append("Constraints:\n" + "\n".join(f"- {c}" for c in self.constraints))
        if self.open_threads: parts.append("Open:\n" + "\n".join(f"- {o}" for o in self.open_threads))
        if self.summary:      parts.append("Summary:\n" + self.summary)
        return "\n\n".join(parts)


@dataclass
class PartnerState:
    rolling_capsule: RollingCapsule = field(default_factory=RollingCapsule)
    window: list[Turn] = field(default_factory=list)
    cycle: int = 0                     # how many compaction cycles have happened
    capsule_paths: list[str] = field(default_factory=list)
    pending_seed: str = ""             # capsule+tail text to inject when the NEXT fork starts


# Injectable side-effects (real impls wire to claude warm-fork / bronze compaction;
# tests inject deterministic fakes so no LLM/network is needed).
ModelFn = Callable[[str], tuple[str, int]]   # (assembled_prompt) -> (response_text, response_tokens)
CompactFn = Callable[[str], dict]            # (prior capsule + window blob) -> {decisions, constraints, open_threads, summary}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def assemble_prompt(state: PartnerState, user_text: str) -> str:
    """Bounded context for one model call: capsule + window + current user msg."""
    parts = []
    rendered = state.rolling_capsule.render()
    if rendered:
        parts.append("## State\n" + rendered)
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
    rolling capsule and REWIND (keeping verbatim tail). Returns True if compacted."""
    cp = (cfg.get("cache_policy") or {})
    capsule_budget = int(cp.get("capsule_budget_tokens", 1500))
    keep_tail = int(cp.get("keep_tail_turns", 0))
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
    # Nothing to compact if the entire window would be kept as tail
    if keep_tail > 0 and len(state.window) <= keep_tail:
        return False
    if keep_tail > 0:
        to_compact = state.window[:-keep_tail]
        tail = state.window[-keep_tail:]
    else:
        to_compact = state.window
        tail = []
    prior_render = state.rolling_capsule.render()
    blob = (prior_render + "\n\n" if prior_render else "") + \
        "\n".join(f"{t.role}: {t.text}" for t in to_compact)
    result = compact_fn(blob)
    # decisions/constraints: append-only, dedup exact dups
    for d in (result.get("decisions") or []):
        if d not in state.rolling_capsule.decisions:
            state.rolling_capsule.decisions.append(d)
    for c in (result.get("constraints") or []):
        if c not in state.rolling_capsule.constraints:
            state.rolling_capsule.constraints.append(c)
    # open_threads and summary: replaced each cycle
    state.rolling_capsule.open_threads = list(result.get("open_threads") or [])
    state.rolling_capsule.summary = result.get("summary") or ""
    state.window = tail                      # REWIND keeping verbatim tail
    state.cycle += 1
    if burnless_root is not None:
        d = Path(burnless_root) / "maestro" / "rolling"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"capsule_{state.cycle}.json"
        rendered = state.rolling_capsule.render()
        p.write_text(
            json.dumps(
                {
                    "cycle": state.cycle,
                    "capsule": rendered,
                    "structured": {
                        "decisions": state.rolling_capsule.decisions,
                        "constraints": state.rolling_capsule.constraints,
                        "open_threads": state.rolling_capsule.open_threads,
                        "summary": state.rolling_capsule.summary,
                    },
                },
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


def _render_tail(state: PartnerState) -> str:
    return "\n".join(f"{t.role}: {t.text}" for t in state.window)


def partner_turn_session(
    state: PartnerState,
    user_text: str,
    *,
    cfg: dict,
    session: MaestroSession,
    runner,                       # RunnerFn passed through to session.send
    compact_fn: CompactFn,        # -> structured dict (maybe_compact contract)
    burnless_root: Optional[Path] = None,
) -> str:
    """One partner turn over a conversation-native session.

    - Sends ONLY the delta (user_text) to the session; when a prior rewind left
      a pending_seed, that seed (capsule.render() + verbatim tail) is injected
      as the new fork's opening via rewind_capsule.
    - Maintains state.window so the should_compact trigger accounting matches
      what the fork has actually accumulated.
    - On compaction: maybe_compact updates the structured capsule + keeps the
      verbatim tail in window; then session.rewind() drops the fork and
      pending_seed = capsule.render() + recent tail is stashed so the NEXT send
      re-forks BASE re-seeded (engine window and fork stay in agreement).
    """
    seed = state.pending_seed or None
    response, rtoks = session.send(user_text, runner=runner, rewind_capsule=seed)
    state.pending_seed = ""                                   # consumed
    state.window.append(Turn("user", user_text, estimate_tokens(user_text)))
    state.window.append(Turn("maestro", response, rtoks))
    if maybe_compact(state, cfg, compact_fn, burnless_root):  # rewinds window to verbatim tail
        session.rewind()                                      # next send will fork BASE
        tail = _render_tail(state)                            # the kept verbatim tail
        cap = state.rolling_capsule.render()
        state.pending_seed = cap + (("\n\n## Recent\n" + tail) if tail else "")
    return response
