from burnless.savings_footer import metrics_from_savings, render_footer


def test_metrics_from_savings_values():
    m = metrics_from_savings(
        {"raw_tokens": 22808, "capsule_tokens": 128, "saved_tokens": 22680, "compression_ratio": 178.19},
        "sonnet",
        1,
    )
    assert m.original_tokens == 22808
    assert m.compressed_tokens == 128
    assert m.saved_tokens == 22680
    assert m.saved_pct > 90
    assert m.real_usd >= m.burnless_usd


def test_render_footer_contains_labels():
    m = metrics_from_savings(
        {"raw_tokens": 22808, "capsule_tokens": 128, "saved_tokens": 22680, "compression_ratio": 178.19},
        "sonnet",
        1,
    )
    footer = render_footer(m)
    assert "Real:" in footer
    assert "Burnless:" in footer
    assert "Saved:" in footer
