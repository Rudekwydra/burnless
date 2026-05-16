from burnless.savings_formula import compute, Savings


def test_zero_when_no_samples():
    s = compute({"compression_ratio_observed_count": 0})
    assert s.total == 0.0
    assert s.linear == 0.0
    assert s.history == 0.0
    assert s.quadratic_bonus == 0.0
    assert s.samples == 0


def test_linear_picked_from_burnless_tokens():
    s = compute({
        "compression_ratio_observed_count": 1,
        "compression_ratio_observed_sum": 3.0,
        "burnless_tokens": 1000,
        "delegation_counter": 1,
    })
    assert s.linear == 1000.0
    assert s.samples == 1


def test_history_grows_with_delegations():
    base = {
        "compression_ratio_observed_count": 2,
        "compression_ratio_observed_sum": 4.0,
        "burnless_tokens": 1000,
    }
    s1 = compute({**base, "delegation_counter": 1})
    s10 = compute({**base, "delegation_counter": 10})
    assert s10.history > s1.history


def test_quadratic_bonus_grows_faster_than_history():
    base = {
        "compression_ratio_observed_count": 2,
        "compression_ratio_observed_sum": 6.0,  # avg ratio 3
        "burnless_tokens": 1000,
    }
    s10 = compute({**base, "delegation_counter": 10})
    s100 = compute({**base, "delegation_counter": 100})
    # quadratic should grow ~100× while history only ~10×
    ratio_quad = s100.quadratic_bonus / max(1.0, s10.quadratic_bonus)
    ratio_hist = s100.history / max(1.0, s10.history)
    assert ratio_quad > ratio_hist


def test_no_crash_on_missing_fields():
    s = compute({})
    assert s.total == 0.0


def test_no_crash_on_bad_types():
    s = compute({
        "compression_ratio_observed_count": None,
        "compression_ratio_observed_sum": None,
        "burnless_tokens": None,
        "delegation_counter": None,
    })
    assert s.total == 0.0


def test_total_is_sum_of_components():
    s = compute({
        "compression_ratio_observed_count": 5,
        "compression_ratio_observed_sum": 15.0,  # avg ratio 3
        "burnless_tokens": 5000,
        "delegation_counter": 20,
    })
    # total should equal sum of non-negative components
    expected = s.linear + s.history + max(0.0, s.quadratic_bonus)
    assert abs(s.total - round(expected, 2)) < 0.01
