from pathlib import Path
import tempfile
from burnless import metrics as metrics_mod
from burnless import dashboard as dashboard_mod


def test_default_metrics_has_legacy_counters():
    from burnless.metrics import DEFAULT_METRICS
    assert "legacy_run_calls" in DEFAULT_METRICS
    assert "legacy_compress_calls" in DEFAULT_METRICS
    assert "legacy_decompress_calls" in DEFAULT_METRICS


def test_bump_legacy_counter_increments():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "metrics.json"
        metrics_mod.bump_legacy_counter(path, "legacy_run_calls")
        metrics_mod.bump_legacy_counter(path, "legacy_run_calls")
        m = metrics_mod.load(path)
        assert m["legacy_run_calls"] == 2


def test_bump_legacy_counter_rejects_unknown():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "metrics.json"
        metrics_mod.bump_legacy_counter(path, "not_a_legacy_counter")
        m = metrics_mod.load(path)
        assert "not_a_legacy_counter" not in m or m.get("not_a_legacy_counter", 0) == 0


def test_dashboard_renders_legacy_section():
    m = dict(metrics_mod.DEFAULT_METRICS)
    m["legacy_run_calls"] = 42
    m["legacy_compress_calls"] = 17
    out = dashboard_mod.render_metrics(m)
    assert "Legacy delegate/run path" in out
    assert "42" in out
    assert "17" in out


def test_dashboard_marks_maestro_only_counters():
    m = dict(metrics_mod.DEFAULT_METRICS)
    out = dashboard_mod.render_metrics(m)
    assert "Maestro only" in out
