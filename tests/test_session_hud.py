from __future__ import annotations

from burnless.session_hud import render_hud, render_explain


def _is_ascii(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


def test_render_hud_off_is_empty():
    assert render_hud({"project": "/x/y", "mode": "default"}, style="off") == ""


def test_render_hud_compact_single_line_ascii():
    out = render_hud(
        {"project": "/home/roberto/antigravity/burnless", "mode": "rolling", "last_status": "OK"},
        style="compact",
    )
    assert out != ""
    assert "\n" not in out
    assert "burnless" in out
    assert _is_ascii(out)


def test_render_hud_verbose_multiline():
    out = render_hud(
        {
            "project": "/home/roberto/proj",
            "mode": "default",
            "last_status": "PART",
            "scope_hash": "sha256:abc",
            "turns": 3,
        },
        style="verbose",
    )
    assert "\n" in out
    assert _is_ascii(out)
    assert "scope_hash" in out


def test_render_hud_tolerates_missing_keys():
    out = render_hud({}, style="compact")
    assert out != ""
    assert _is_ascii(out)


def test_render_explain_all_none_six_times():
    sections = {
        "active_mode": None,
        "last_hook_injection": None,
        "last_compaction_decision": None,
        "last_route_decision": None,
        "last_retrieval": None,
        "last_delegation_status": None,
    }
    out = render_explain(sections)
    # render_explain now renders 7 lines; the 7th (last_warm_status) is absent
    # from `sections`, so it also renders as "(none recorded)".
    assert out.count("(none recorded)") == 7
    assert _is_ascii(out)


def test_render_explain_shows_last_delegation_status():
    sections = {
        "active_mode": "default",
        "last_hook_injection": None,
        "last_compaction_decision": None,
        "last_route_decision": None,
        "last_retrieval": None,
        "last_delegation_status": "OK",
    }
    out = render_explain(sections)
    assert "OK" in out
    assert _is_ascii(out)
