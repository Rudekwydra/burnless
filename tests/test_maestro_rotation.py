"""Slice-3: rolling-memory epoch rotation wired into Maestro fork lifecycle."""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest


NEVER_MATCH = re.compile(r"(?!)")


def make_fake_session_cls(rewound_record=None, reseed_record=None):
    """Build an isolated FakeSession class that records rewind/send calls."""
    _rewound = rewound_record if rewound_record is not None else []
    _reseed = reseed_record if reseed_record is not None else []

    class FakeSession:
        def __init__(self, *, base_uuid, model, claude_bin, fork_session_id=None):
            self.fork_session_id = fork_session_id
            self.usages = []

        def send(self, msg, *, runner, rewind_capsule=None):
            _reseed.append(rewind_capsule)
            self.fork_session_id = "f1"
            return ("`brz x :: OK` env", 5)

        def rewind(self):
            self.fork_session_id = None
            _rewound.append(True)

    return FakeSession


def invoke_pe(project_root, cfg=None, SessionCls=None):
    from burnless.maestro_layer import process_envelope

    if cfg is None:
        cfg = {}
    if SessionCls is None:
        SessionCls = make_fake_session_cls()

    fake_paths = {
        "config": str(project_root / ".burnless" / "config.yaml"),
        "state": str(project_root / ".burnless" / "state.json"),
    }

    with (
        patch("burnless.maestro.base.maestro_base_init", return_value="BASE"),
        patch("burnless.maestro.base.maestro_iso_cwd", return_value=project_root),
        patch("burnless.warm_session._claude_binary", return_value="claude"),
        patch("burnless.maestro.runners.runner_claude_json", return_value=lambda *a, **kw: None),
        patch("burnless.maestro.session_runner.MaestroSession", SessionCls),
        patch("burnless.maestro.dispatcher.DELEGATE_RE", NEVER_MATCH),
        patch("burnless.maestro.dispatcher.DELEGATE_SHORT_RE", NEVER_MATCH),
        patch("burnless.config.load", return_value=cfg),
        patch("burnless.state.load", return_value={}),
        patch("burnless.paths.paths_for", return_value=fake_paths),
        patch("burnless.epochs.epoch_summarizer", return_value=lambda t: "SUM"),
    ):
        return process_envelope("hello", project_root)


@pytest.fixture
def project_root(tmp_path):
    return tmp_path


# ── test_disabled_no_epoch_calls ──────────────────────────────────────────────

def test_disabled_no_epoch_calls(project_root):
    """epochs.enabled absent → no epoch files written anywhere."""
    invoke_pe(project_root, cfg={})
    epochs_dir = project_root / ".burnless" / "epochs"
    if epochs_dir.exists():
        md_files = list(epochs_dir.rglob("*.md"))
        assert not md_files, f"unexpected epoch files with epochs disabled: {md_files}"


# ── test_enabled_captures_turn ────────────────────────────────────────────────

def test_enabled_captures_turn(project_root):
    """epochs.enabled true → exactly one 001.md written after a single turn."""
    invoke_pe(project_root, cfg={"epochs": {"enabled": True}})

    epochs_root = project_root / ".burnless" / "epochs"
    assert epochs_root.exists(), "epochs dir not created"
    chat_dirs = [d for d in epochs_root.iterdir() if d.is_dir()]
    assert len(chat_dirs) == 1, f"expected 1 chat dir, got {[d.name for d in chat_dirs]}"
    chat_dir = chat_dirs[0]
    assert chat_dir.name.startswith("maestro-"), f"unexpected dir name: {chat_dir.name}"
    md_files = [f for f in chat_dir.iterdir() if f.is_file() and f.suffix == ".md"]
    assert len(md_files) == 1, f"expected 1 .md file, got {[f.name for f in md_files]}"
    assert md_files[0].name == "001.md"


# ── test_rotates_and_reseeds ──────────────────────────────────────────────────

def test_rotates_and_reseeds(project_root):
    """10 turns trigger consolidation + rewind; 11th call re-seeds from active_chain."""
    cfg = {"epochs": {"enabled": True}}
    rewound: list = []

    for _ in range(10):
        SessionCls = make_fake_session_cls(rewound_record=rewound)
        invoke_pe(project_root, cfg=cfg, SessionCls=SessionCls)

    assert rewound, "rewind() was not called after 10 turns — rotation did not fire"

    epochs_root = project_root / ".burnless" / "epochs"
    a01_files = list(epochs_root.rglob("a01.md"))
    assert a01_files, (
        f"a01.md not found after 10 turns; dir contents: {list(epochs_root.rglob('*'))}"
    )

    reseed_11: list = []
    SessionCls11 = make_fake_session_cls(rewound_record=[], reseed_record=reseed_11)
    invoke_pe(project_root, cfg=cfg, SessionCls=SessionCls11)

    assert reseed_11, "send() not called on 11th turn"
    capsule = reseed_11[0]
    assert capsule is not None and capsule != "", (
        f"rewind_capsule was empty/None on 11th call: {capsule!r}"
    )
