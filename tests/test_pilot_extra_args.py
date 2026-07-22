"""P11.1: pilot extra_args normalization — the `--` separator must never
reach the host argv (it turns capability flags into prompt text)."""

import pytest

from burnless.cli import _pilot_normalize_extra_args


def test_strips_leading_separator():
    extra, err = _pilot_normalize_extra_args(["--", "--chrome"], chrome=False, host_name="claude")
    assert err is None
    assert extra == ["--chrome"]


def test_clean_args_pass_through():
    extra, err = _pilot_normalize_extra_args(["--verbose"], chrome=False, host_name="claude")
    assert err is None
    assert extra == ["--verbose"]


def test_chrome_flag_appends_on_claude():
    extra, err = _pilot_normalize_extra_args([], chrome=True, host_name="claude")
    assert err is None
    assert extra == ["--chrome"]


def test_chrome_flag_never_duplicates():
    extra, err = _pilot_normalize_extra_args(["--", "--chrome"], chrome=True, host_name="claude")
    assert err is None
    assert extra == ["--chrome"]


def test_chrome_on_codex_fails_loud():
    extra, err = _pilot_normalize_extra_args([], chrome=True, host_name="codex")
    assert extra is None
    assert err is not None and "codex" in err


def test_empty_input_is_empty():
    extra, err = _pilot_normalize_extra_args(None, chrome=False, host_name="claude")
    assert err is None
    assert extra == []
