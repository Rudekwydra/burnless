"""Test that Fable 5 is present in worker menu options and ranks first among Anthropic models."""
from burnless.menu import worker_menu_options


def test_fable_in_menu():
    """Fable entry exists with correct provider, model, and spec."""
    opts = worker_menu_options({"anthropic": True})
    fable_entries = [o for o in opts if o.get("model") == "fable"]
    assert len(fable_entries) == 1, "Fable entry must exist exactly once"
    fable = fable_entries[0]
    assert fable["provider"] == "anthropic"
    assert fable["spec"] == "anthropic:fable"


def test_fable_before_opus():
    """Fable entry comes before Opus in the returned list."""
    opts = worker_menu_options({"anthropic": True})
    fable_idx = next(i for i, o in enumerate(opts) if o.get("model") == "fable")
    opus_idx = next(i for i, o in enumerate(opts) if o.get("model") == "opus")
    assert fable_idx < opus_idx, "Fable must come before Opus"
