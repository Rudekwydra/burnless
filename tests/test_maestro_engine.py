"""Deterministic tests for the maestro partner engine (M1 prototype).

No LLM/network/subprocess: model_fn and compact_fn are injected fakes.
"""

from __future__ import annotations

from pathlib import Path

from burnless.maestro import engine
from burnless.maestro.engine import (
    PartnerState,
    Turn,
    assemble_prompt,
    maybe_compact,
    partner_turn,
    window_tokens,
)

# NOTE: with S=150 (capsule_budget_tokens) and M=0 (compaction_cost_tokens),
# B* = 150 + (2.0*150+0)/(8*0.10) = 525 < min_hot_tail=1500, so the
# min_hot_tail gate is the binding constraint — compaction fires at window ≥ 1500,
# same as the old proportional-ratio-0.10 behavior.
CFG = {
    "cache_policy": {
        "cache_read_ratio": 0.10,
        "cache_write_ratio": 2.0,
        "expected_future_turns": 8,
        "min_hot_tail_tokens": 1500,
        "capsule_budget_tokens": 150,   # constant S=150 → B*=525 < min_hot_tail
        "compaction_cost_tokens": 0,    # M=0: fires as soon as window >= min_hot_tail
    }
}

BIG_RESPONSE_TOKENS = 600  # per maestro turn; user msg adds ~250 → window grows fast


def big_model_fn(prompt: str) -> tuple[str, int]:
    return ("maestro-response " + "x" * 50, BIG_RESPONSE_TOKENS)


def tiny_model_fn(prompt: str) -> tuple[str, int]:
    return ("ok", 2)


def make_compact_fn(state: PartnerState):
    def compact_fn(blob: str) -> str:
        return f"CAP{state.cycle + 1}"
    return compact_fn


def run_turns(state, n, model_fn, user_text, **kw):
    max_window = 0
    for _ in range(n):
        partner_turn(
            state,
            user_text,
            cfg=CFG,
            model_fn=model_fn,
            compact_fn=kw.get("compact_fn") or make_compact_fn(state),
            burnless_root=kw.get("burnless_root"),
        )
        max_window = max(max_window, window_tokens(state))
    return max_window


def test_window_stays_bounded_over_many_turns():
    state = PartnerState()
    user_text = "u" * 1000  # ~250 tokens estimated
    per_turn = engine.estimate_tokens(user_text) + BIG_RESPONSE_TOKENS  # ~850
    max_window = run_turns(state, 30, big_model_fn, user_text)
    # min_hot_tail=1500 → compaction can only fire once window ≥ 1500; with
    # ~850 tokens/turn it fires on the 2nd turn of each cycle, so the window
    # never exceeds 2 turns' worth. Bound: < 3x a single turn's tokens and
    # < min_hot_tail * 3.
    assert max_window < 3 * per_turn
    assert max_window < 1500 * 3
    # window was actually rewound at least once during the run
    assert window_tokens(state) <= max_window


def test_cycle_increments_repeatedly():
    state = PartnerState()
    run_turns(state, 30, big_model_fn, "u" * 1000)
    assert state.cycle >= 2


def test_rolling_capsule_nonempty_after_first_compaction():
    state = PartnerState()
    user_text = "u" * 1000
    for i in range(30):
        partner_turn(
            state, user_text, cfg=CFG,
            model_fn=big_model_fn, compact_fn=make_compact_fn(state),
        )
        if state.cycle >= 1:
            break
    assert state.cycle >= 1
    assert state.rolling_capsule == "CAP1"


def test_capsules_written_to_disk(tmp_path):
    state = PartnerState()
    run_turns(state, 30, big_model_fn, "u" * 1000, burnless_root=tmp_path)
    assert state.cycle >= 2
    rolling = tmp_path / "maestro" / "rolling"
    files = sorted(
        rolling.glob("capsule_*.json"),
        key=lambda p: int(p.stem.split("_")[1]),
    )
    assert len(files) == state.cycle
    assert (rolling / "capsule_1.json").exists()
    import json
    data = json.loads((rolling / f"capsule_{state.cycle}.json").read_text(encoding="utf-8"))
    assert data["cycle"] == state.cycle
    assert data["capsule"] == state.rolling_capsule
    assert state.capsule_paths == [str(f) for f in files]


def test_tiny_turns_never_compact():
    state = PartnerState()
    run_turns(state, 30, tiny_model_fn, "hi")
    assert state.cycle == 0
    assert state.rolling_capsule == ""
    assert state.capsule_paths == []
    # window holds all 60 tiny turns but stays below min_hot_tail
    assert len(state.window) == 60
    assert window_tokens(state) < 1500


def test_assemble_prompt_user_seen_exactly_once():
    state = PartnerState(rolling_capsule="CAPX")
    state.window.append(Turn("user", "earlier", 2))
    state.window.append(Turn("maestro", "earlier-reply", 3))
    seen = {}

    def spy_model(prompt: str) -> tuple[str, int]:
        seen["prompt"] = prompt
        return ("reply", 5)

    partner_turn(
        state, "current question", cfg=CFG,
        model_fn=spy_model, compact_fn=lambda blob: "CAP",
    )
    prompt = seen["prompt"]
    assert prompt.count("user: current question") == 1
    assert "## State\nCAPX" in prompt
    assert "user: earlier" in prompt
    # after the turn the window holds both the user msg and the response
    assert [t.role for t in state.window[-2:]] == ["user", "maestro"]


def test_maybe_compact_below_threshold_is_noop():
    state = PartnerState()
    state.window.append(Turn("user", "small", 100))
    assert maybe_compact(state, CFG, lambda blob: "CAP") is False
    assert state.cycle == 0
    assert len(state.window) == 1


def test_compact_blob_includes_prior_capsule_and_window():
    state = PartnerState(rolling_capsule="OLDCAP")
    state.window.append(Turn("user", "big question", 2000))
    blobs = []

    def capture_compact(blob: str) -> str:
        blobs.append(blob)
        return "NEWCAP"

    assert maybe_compact(state, CFG, capture_compact) is True
    assert blobs[0].startswith("OLDCAP\n\n")
    assert "user: big question" in blobs[0]
    assert state.rolling_capsule == "NEWCAP"
    assert state.window == []
    assert state.cycle == 1


def test_assemble_prompt_without_capsule_has_no_state_header():
    state = PartnerState()
    prompt = assemble_prompt(state, "hello")
    assert prompt == "user: hello"


def test_trigger_is_size_driven():
    """Compaction trigger must be SIZE-DRIVEN: small window no-compact, large window compact.

    With DEFAULT_CONFIG cache_policy: S=1500, M=4000, w=2.0, K=8, r=0.10
    B* = S + (w*S + M)/(K*r) = 1500 + (2.0*1500+4000)/(8*0.10) = 10250

    A window far below B* must NOT compact; a window far above B* MUST compact.
    This was broken before the fix: proportional S cancelled B in the formula,
    making the decision independent of window size.
    """
    from burnless.config import DEFAULT_CONFIG
    cfg = {"cache_policy": DEFAULT_CONFIG["cache_policy"]}
    cp = cfg["cache_policy"]
    S = cp["capsule_budget_tokens"]   # 1500
    M = cp["compaction_cost_tokens"]  # 4000
    w = cp["cache_write_ratio"]       # 2.0
    K = cp["expected_future_turns"]   # 8
    r = cp["cache_read_ratio"]        # 0.10
    b_star = int(S + (w * S + M) / (K * r))  # 10250

    small = PartnerState(window=[Turn("user", "x", b_star // 2)])  # 5125 tokens < B*
    big = PartnerState(window=[Turn("user", "x", b_star * 3)])     # 30750 tokens > B*

    assert maybe_compact(small, cfg, lambda b: "C") is False, "small window should NOT compact"
    assert maybe_compact(big, cfg, lambda b: "C") is True, "huge window SHOULD compact"
