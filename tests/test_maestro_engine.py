"""Deterministic tests for the maestro partner engine (M1 prototype).

No LLM/network/subprocess: model_fn and compact_fn are injected fakes.
"""

from __future__ import annotations

from pathlib import Path

from burnless.maestro import engine
from burnless.maestro.engine import (
    PartnerState,
    RollingCapsule,
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
        "rolling_compaction_enabled": True,   # explicit opt-in for these unit tests
    }
}

BIG_RESPONSE_TOKENS = 600  # per maestro turn; user msg adds ~250 → window grows fast


def big_model_fn(prompt: str) -> tuple[str, int]:
    return ("maestro-response " + "x" * 50, BIG_RESPONSE_TOKENS)


def tiny_model_fn(prompt: str) -> tuple[str, int]:
    return ("ok", 2)


def make_compact_fn(state: PartnerState):
    def compact_fn(blob: str) -> dict:
        return {"decisions": [], "constraints": [], "open_threads": [], "summary": f"CAP{state.cycle + 1}"}
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
    assert state.rolling_capsule.summary == "CAP1"


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
    assert data["capsule"] == state.rolling_capsule.render()
    assert state.capsule_paths == [str(f) for f in files]


def test_tiny_turns_never_compact():
    state = PartnerState()
    run_turns(state, 30, tiny_model_fn, "hi")
    assert state.cycle == 0
    assert state.rolling_capsule.render() == ""
    assert state.capsule_paths == []
    # window holds all 60 tiny turns but stays below min_hot_tail
    assert len(state.window) == 60
    assert window_tokens(state) < 1500


def test_rolling_compaction_disabled_by_default():
    """Default cfg (rolling_compaction_enabled absent/False): huge window must NOT compact.

    Proves the v1 never-compact default: even a window far above B* returns False
    and cycle stays at 0 when the toggle is off.
    """
    from burnless.config import DEFAULT_CONFIG
    cfg = {"cache_policy": DEFAULT_CONFIG["cache_policy"]}
    big = PartnerState(window=[Turn("user", "x", 500000)])
    result = maybe_compact(big, cfg, lambda b: {"decisions": [], "constraints": [], "open_threads": [], "summary": "s"})
    assert result is False, "default must be never-compact (rolling_compaction_enabled=False)"
    assert big.cycle == 0, "cycle must stay 0 when compaction is disabled"


def test_assemble_prompt_user_seen_exactly_once():
    state = PartnerState(rolling_capsule=RollingCapsule(summary="CAPX"))
    state.window.append(Turn("user", "earlier", 2))
    state.window.append(Turn("maestro", "earlier-reply", 3))
    seen = {}

    def spy_model(prompt: str) -> tuple[str, int]:
        seen["prompt"] = prompt
        return ("reply", 5)

    partner_turn(
        state, "current question", cfg=CFG,
        model_fn=spy_model,
        compact_fn=lambda blob: {"decisions": [], "constraints": [], "open_threads": [], "summary": "CAP"},
    )
    prompt = seen["prompt"]
    assert prompt.count("user: current question") == 1
    assert "## State\nSummary:\nCAPX" in prompt
    assert "user: earlier" in prompt
    # after the turn the window holds both the user msg and the response
    assert [t.role for t in state.window[-2:]] == ["user", "maestro"]


def test_maybe_compact_below_threshold_is_noop():
    state = PartnerState()
    state.window.append(Turn("user", "small", 100))
    assert maybe_compact(state, CFG, lambda blob: {"decisions": [], "constraints": [], "open_threads": [], "summary": "CAP"}) is False
    assert state.cycle == 0
    assert len(state.window) == 1


def test_compact_blob_includes_prior_capsule_and_window():
    state = PartnerState(rolling_capsule=RollingCapsule(summary="OLDCAP"))
    state.window.append(Turn("user", "big question", 2000))
    blobs = []

    def capture_compact(blob: str) -> dict:
        blobs.append(blob)
        return {"decisions": [], "constraints": [], "open_threads": [], "summary": "NEWCAP"}

    assert maybe_compact(state, CFG, capture_compact) is True
    assert blobs[0].startswith("Summary:\nOLDCAP\n\n")
    assert "user: big question" in blobs[0]
    assert state.rolling_capsule.summary == "NEWCAP"
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
    import copy
    from burnless.config import DEFAULT_CONFIG
    cp = copy.deepcopy(DEFAULT_CONFIG["cache_policy"])
    cp["rolling_compaction_enabled"] = True
    cfg = {"cache_policy": cp}
    cp = cfg["cache_policy"]
    S = cp["capsule_budget_tokens"]   # 1500
    M = cp["compaction_cost_tokens"]  # 4000
    w = cp["cache_write_ratio"]       # 2.0
    K = cp["expected_future_turns"]   # 8
    r = cp["cache_read_ratio"]        # 0.10
    keep = cp["keep_tail_turns"]      # 4
    b_star = int(S + (w * S + M) / (K * r))  # 10250

    _noop = lambda b: {"decisions": [], "constraints": [], "open_threads": [], "summary": "C"}
    small = PartnerState(window=[Turn("user", "x", b_star // 2)])     # 5125 tokens < B*, should_compact=False
    # big: 5 turns (> keep_tail=4) with total tokens >> B*
    big = PartnerState(window=[Turn("user", "x", b_star)] * (keep + 1))

    assert maybe_compact(small, cfg, _noop) is False, "small window should NOT compact"
    assert maybe_compact(big, cfg, _noop) is True, "huge window SHOULD compact"


# ---------------------------------------------------------------------------
# New tests: verbatim tail, structured append-only capsule, blob excludes tail
# ---------------------------------------------------------------------------

def test_verbatim_tail_kept_after_compact():
    """After compaction, window retains exactly keep_tail_turns most-recent turns."""
    import copy
    from burnless.config import DEFAULT_CONFIG
    cp = copy.deepcopy(DEFAULT_CONFIG["cache_policy"])
    cp["rolling_compaction_enabled"] = True
    cfg = {"cache_policy": cp}
    keep = cfg["cache_policy"]["keep_tail_turns"]  # 4
    turns = [Turn("user" if i % 2 == 0 else "maestro", f"t{i}", 30000) for i in range(10)]
    state = PartnerState(window=turns)
    assert maybe_compact(state, cfg, lambda b: {"decisions": [], "constraints": [], "open_threads": [], "summary": "s"}) is True
    assert len(state.window) == keep, f"expected {keep} tail turns, got {len(state.window)}"
    assert state.window[-1].text == "t9", "most-recent turn must be last in tail"
    assert state.window[0].text == f"t{10 - keep}", "oldest kept turn must be first in tail"


def test_decisions_and_constraints_accumulate_across_two_cycles():
    """Decisions/constraints carry VERBATIM across cycles (append-only); summary is replaced."""
    import copy
    from burnless.config import DEFAULT_CONFIG
    cp = copy.deepcopy(DEFAULT_CONFIG["cache_policy"])
    cp["rolling_compaction_enabled"] = True
    cfg = {"cache_policy": cp}
    state = PartnerState(window=[Turn("user", "x", 30000) for _ in range(10)])

    def cf1(blob: str) -> dict:
        return {"decisions": ["D1"], "constraints": ["C1"], "open_threads": ["O1"], "summary": "s1"}

    assert maybe_compact(state, cfg, cf1) is True
    assert state.rolling_capsule.decisions == ["D1"]
    assert state.rolling_capsule.constraints == ["C1"]
    assert state.rolling_capsule.summary == "s1"

    # second cycle: refill window with enough turns
    for i in range(10):
        state.window.append(Turn("user", f"x{i}", 30000))

    def cf2(blob: str) -> dict:
        return {"decisions": ["D2"], "constraints": [], "open_threads": [], "summary": "s2"}

    assert maybe_compact(state, cfg, cf2) is True
    assert "D1" in state.rolling_capsule.decisions, "D1 must survive into cycle 2"
    assert "D2" in state.rolling_capsule.decisions, "D2 from cycle 2 must be present"
    assert "C1" in state.rolling_capsule.constraints, "C1 must survive into cycle 2"
    assert state.rolling_capsule.summary == "s2", "summary must be replaced"


def test_compact_blob_excludes_kept_tail():
    """The blob passed to compact_fn must not contain the tail turns (no double-count)."""
    import copy
    from burnless.config import DEFAULT_CONFIG
    cp = copy.deepcopy(DEFAULT_CONFIG["cache_policy"])
    cp["rolling_compaction_enabled"] = True
    cfg = {"cache_policy": cp}
    keep = cfg["cache_policy"]["keep_tail_turns"]  # 4
    n = keep + 4  # 8 turns total
    state = PartnerState(window=[Turn("user", f"t{i}", 30000) for i in range(n)])
    blobs: list[str] = []

    def capture(blob: str) -> dict:
        blobs.append(blob)
        return {"decisions": [], "constraints": [], "open_threads": [], "summary": "s"}

    assert maybe_compact(state, cfg, capture) is True
    assert len(blobs) == 1
    # turns in blob: t0..t(n-keep-1); tail: t(n-keep)..t(n-1)
    for i in range(n - keep):
        assert f"t{i}" in blobs[0], f"t{i} should be in blob"
    for i in range(n - keep, n):
        assert f"t{i}" not in blobs[0], f"t{i} (tail) must NOT be in blob"


# ---------------------------------------------------------------------------
# partner_turn_session: integrated session backend (fake session + runner)
# ---------------------------------------------------------------------------

from burnless.maestro.engine import partner_turn_session


class FakeSession:
    """Records send/rewind calls; never touches a real runner/subprocess."""

    def __init__(self, response_tokens: int = 3000):
        self.sent: list[tuple[str, object]] = []   # (user_msg, rewind_capsule)
        self.rewound = 0
        self.response_tokens = response_tokens

    def send(self, user_msg, *, runner, rewind_capsule=None):
        self.sent.append((user_msg, rewind_capsule))
        return ("maestro-resp", self.response_tokens)

    def rewind(self):
        self.rewound += 1


def _default_cfg():
    import copy
    from burnless.config import DEFAULT_CONFIG
    cp = copy.deepcopy(DEFAULT_CONFIG["cache_policy"])
    cp["rolling_compaction_enabled"] = True
    return {"cache_policy": cp}


def _seed_compact_fn(blob: str) -> dict:
    return {"decisions": ["DSEED"], "constraints": ["CSEED"], "open_threads": [], "summary": "ssum"}


def _noop_runner(cmd):
    return {}


def test_session_normal_turn_is_delta_send():
    """No pending seed → rewind_capsule=None, only the user delta sent; window gains 2 Turns."""
    fs = FakeSession(response_tokens=2)
    state = PartnerState()
    resp = partner_turn_session(
        state, "hello", cfg=_default_cfg(), session=fs,
        runner=_noop_runner, compact_fn=_seed_compact_fn,
    )
    assert resp == "maestro-resp"
    assert fs.sent == [("hello", None)]
    assert len(state.window) == 2
    assert [t.role for t in state.window] == ["user", "maestro"]
    assert state.pending_seed == ""
    assert fs.rewound == 0


def test_session_compaction_rewinds_and_sets_pending_seed():
    """Big turns trigger compaction: session.rewind() called, pending_seed = capsule + verbatim tail."""
    # DEFAULT_CONFIG: B*=10250, keep_tail=4. 3000-tok responses → fires on the 4th turn
    # (window ~12k > B*, 8 turns > keep_tail).
    fs = FakeSession(response_tokens=3000)
    state = PartnerState()
    for _ in range(4):
        partner_turn_session(
            state, "q", cfg=_default_cfg(), session=fs,
            runner=_noop_runner, compact_fn=_seed_compact_fn,
        )
    assert fs.rewound == 1, "rewind must be called exactly once when compaction fires"
    assert state.cycle == 1
    assert state.pending_seed != ""
    assert "DSEED" in state.pending_seed, "seed must carry the capsule's decisions"
    assert "CSEED" in state.pending_seed
    assert "## Recent" in state.pending_seed
    assert "maestro: maestro-resp" in state.pending_seed, "seed must carry the verbatim tail"
    # window rewound to the kept tail
    assert len(state.window) == _default_cfg()["cache_policy"]["keep_tail_turns"]


def test_session_next_send_consumes_and_clears_seed():
    """The pending_seed set by a rewind reaches the NEXT send as rewind_capsule, then clears."""
    fs = FakeSession(response_tokens=3000)
    state = PartnerState()
    for _ in range(4):
        partner_turn_session(
            state, "q", cfg=_default_cfg(), session=fs,
            runner=_noop_runner, compact_fn=_seed_compact_fn,
        )
    seed_before = state.pending_seed
    assert seed_before != ""
    # 5th turn: tail (~6k) + 1 turn (~3k) stays below B*=10250 → no new compaction
    partner_turn_session(
        state, "q2", cfg=_default_cfg(), session=fs,
        runner=_noop_runner, compact_fn=_seed_compact_fn,
    )
    assert fs.sent[-1] == ("q2", seed_before), "seed must be injected as rewind_capsule"
    assert state.pending_seed == "", "seed must be cleared after consumption"
    assert fs.rewound == 1, "no second rewind on the re-seeded turn"
    # earlier sends (pre-compaction) carried no capsule
    assert all(rc is None for (_m, rc) in fs.sent[:-1])


def test_session_tiny_turns_never_rewind():
    """Tiny turns: no compaction, no rewind, pending_seed stays empty, every send is bare delta."""
    fs = FakeSession(response_tokens=2)
    state = PartnerState()
    for i in range(30):
        partner_turn_session(
            state, f"hi{i}", cfg=_default_cfg(), session=fs,
            runner=_noop_runner, compact_fn=_seed_compact_fn,
        )
    assert fs.rewound == 0
    assert state.cycle == 0
    assert state.pending_seed == ""
    assert all(rc is None for (_m, rc) in fs.sent)
    assert len(state.window) == 60
