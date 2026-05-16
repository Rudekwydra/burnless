import tempfile
from pathlib import Path
from burnless import metrics as metrics_mod


def test_default_metrics_has_ratio_fields():
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "m.json"
        m = metrics_mod.load(p)
        assert m.get("compression_ratio_observed_sum") == 0.0
        assert m.get("compression_ratio_observed_count") == 0


def test_bump_ratio_observed_accumulates():
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "m.json"
        metrics_mod.bump_ratio_observed(p, 3.5)
        metrics_mod.bump_ratio_observed(p, 2.0)
        m = metrics_mod.load(p)
        assert m["compression_ratio_observed_sum"] == 5.5
        assert m["compression_ratio_observed_count"] == 2


def test_dashboard_renders_ratio_when_count_positive():
    from burnless.dashboard import render
    metrics = {
        "compression_ratio_observed_sum": 6.84,
        "compression_ratio_observed_count": 2,
    }
    output = render(metrics)
    assert "Observed compression" in output
    assert "3.42" in output  # 6.84 / 2
    assert "samples" in output.lower()


def test_dashboard_omits_ratio_when_count_zero():
    from burnless.dashboard import render
    metrics = {
        "compression_ratio_observed_sum": 0.0,
        "compression_ratio_observed_count": 0,
    }
    output = render(metrics)
    assert "Observed compression" not in output
