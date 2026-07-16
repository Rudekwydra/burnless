from __future__ import annotations

from burnless.pilot.cadence import CadenceConfig, CompactDecision, decide_compaction
from burnless.pilot.core import ContextUsage


def U(cur, lim):
    return ContextUsage(current=cur, limit=lim)


def test_below_min_backlog_never_compacts():
    result = decide_compaction(U(190000, 200000), is_idle=True, backlog_turns=2)
    assert result.should is False
    assert result.urgency == "none"


def test_hard_ceiling_forces_even_when_busy():
    result = decide_compaction(U(180000, 200000), is_idle=False, backlog_turns=8)
    assert result.should is True
    assert result.urgency == "forced"


def test_soft_ceiling_plus_idle():
    result = decide_compaction(U(150000, 200000), is_idle=True, backlog_turns=8)
    assert result.should is True
    assert result.urgency == "idle"


def test_soft_ceiling_but_busy_waits():
    result = decide_compaction(U(150000, 200000), is_idle=False, backlog_turns=8)
    assert result.should is False
    assert result.urgency == "none"


def test_unknown_usage_idle_large_backlog():
    result = decide_compaction(U(None, None), is_idle=True, backlog_turns=12)
    assert result.should is True
    assert result.urgency == "idle"


def test_unknown_usage_idle_small_backlog():
    result = decide_compaction(U(None, None), is_idle=True, backlog_turns=5)
    assert result.should is False
    assert result.urgency == "none"


def test_low_usage_idle_no_trigger():
    result = decide_compaction(U(50000, 200000), is_idle=True, backlog_turns=8)
    assert result.should is False
    assert result.urgency == "none"


def test_custom_config_thresholds():
    cfg = CadenceConfig(soft_ceiling_ratio=0.5)
    result = decide_compaction(U(120000, 200000), is_idle=True, backlog_turns=8, cfg=cfg)
    assert result.should is True
    assert result.urgency == "idle"


def test_limit_zero_is_treated_as_unknown():
    result = decide_compaction(U(100, 0), is_idle=True, backlog_turns=12)
    assert result.should is True
    assert result.urgency == "idle"
