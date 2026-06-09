"""Tests for the config spine (coreconfig) — single source of truth for tiers."""
from __future__ import annotations

import textwrap

import pytest

from burnless.coreconfig import resolver, schema


def test_all_four_tiers_present():
    assert set(schema.DEFAULT_TIERS) >= {"bronze", "silver", "gold", "diamond"}


def test_resolve_model_mirrors_default_tier_models():
    # Mirrored from config.DEFAULT_TIER_MODELS (+ diamond=opus escalation).
    assert resolver.resolve_model("bronze") == "claude-haiku-4-5-20251001"
    assert resolver.resolve_model("silver") == "claude-sonnet-4-6"
    assert resolver.resolve_model("gold") == "claude-opus-4-8"
    assert resolver.resolve_model("diamond") == "claude-opus-4-8"


def test_resolve_priority_ordering():
    p = resolver.resolve_priority
    assert p("diamond") > p("gold") > p("silver") > p("bronze")


def _write_cfg(root, body: str):
    d = root / ".burnless"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return root


def test_cascade_precedence_project_over_global_over_default(tmp_path, monkeypatch):
    # Fake HOME so the global config path is controllable.
    home = tmp_path / "home"
    (home / ".config" / "burnless").mkdir(parents=True)
    monkeypatch.setattr(resolver.Path, "home", staticmethod(lambda: home))

    # default (no global, no project) -> spine value
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = resolver.load(proj)
    assert resolver.resolve_model("silver", cfg) == "claude-sonnet-4-6"

    # global override
    (home / ".config" / "burnless" / "config.yaml").write_text(
        "agents:\n  silver:\n    model: global-sonnet\n", encoding="utf-8"
    )
    cfg = resolver.load(proj)
    assert resolver.resolve_model("silver", cfg) == "global-sonnet"

    # project override wins over global
    _write_cfg(proj, "agents:\n  silver:\n    model: project-sonnet\n")
    cfg = resolver.load(proj)
    assert resolver.resolve_model("silver", cfg) == "project-sonnet"


def test_resolve_model_command_token_override(tmp_path):
    cfg = {"agents": {"gold": {"command": "claude --model opus -p"}}}
    # alias opus -> canonical opus id
    assert resolver.resolve_model("gold", cfg) == "claude-opus-4-8"


def test_route_picks_gold_silver_bronze():
    assert resolver.route("nova arquitetura do sistema")[0] == "gold"
    assert resolver.route("escrever documentação do projeto")[0] == "silver"
    assert resolver.route("resumir este log")[0] == "bronze"
    # path hint -> silver
    assert resolver.route("olhar /Users/roberto/foo.py") == ("silver", "path")
    # nothing matches -> default bronze
    assert resolver.route("xyzzy nothing here")[0] == "bronze"


def test_route_respects_cfg_keyword_override():
    cfg = {"routing": {"gold": ["frobnicate"]}}
    assert resolver.route("please frobnicate this", cfg)[0] == "gold"


def test_single_source_proof(monkeypatch):
    # Mutate the ONE source of truth; resolver must reflect it.
    monkeypatch.setattr(
        schema.DEFAULT_TIERS["silver"], "model", "spine-changed-model"
    )
    assert resolver.resolve_model("silver") == "spine-changed-model"
