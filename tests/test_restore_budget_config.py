"""P6/A2: restore budgets resolve from config when the flag is absent.

epochs.restore_budget_tokens (default 4000) → source=clear
epochs.startup_budget_tokens (default 2000) → source=startup
--budget-tokens on the CLI stays as an explicit override.
"""
from __future__ import annotations

import argparse
import json

import pytest

from burnless import cli, recovery


@pytest.fixture(autouse=True)
def _isolate_global_config(monkeypatch):
    # config.load must not pick up the developer's ~/.config/burnless/config.yaml
    monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", "")
    monkeypatch.delenv("BURNLESS_PROFILE", raising=False)


def _seed(root, n_pending=6, pending_chars=3000):
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo denso\n\n## Threads abertas\n- thread viva\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    for i in range(1, n_pending + 1):
        filler = f"conteudo {i} " * (pending_chars // 12)
        recovery.journal_append(
            root,
            {
                "schema": 1,
                "host": "claude",
                "host_session_id": "sid-1",
                "process_instance_id": "proc-1",
                "transcript_path": "/tmp/t.jsonl",
                "exchange_id": f"sha256:budget-{i}",
                "user_text": f"pergunta {i}\n{filler}",
                "assistant_text": f"resposta {i}\n{filler}",
                "files": [],
            },
        )


def _restore(root, source="clear", budget_tokens=None):
    return recovery.render_restore(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source=source,
        budget_tokens=budget_tokens,
    )


def _ctx(payload):
    return payload["hookSpecificOutput"]["additionalContext"]


def test_clear_restore_defaults_to_4000_tokens(tmp_path):
    root = tmp_path / ".burnless"
    _seed(root)

    ctx = _ctx(_restore(root, source="clear"))
    # payload (~38k chars raw) is cut to the 4000-token default, not the old 2000
    assert len(ctx) <= 4000 * 4
    assert len(ctx) > 2000 * 4


def test_startup_restore_defaults_to_2000_tokens(tmp_path):
    root = tmp_path / ".burnless"
    _seed(root)

    ctx = _ctx(_restore(root, source="startup"))
    assert len(ctx) <= 2000 * 4


def test_config_overrides_both_budgets(tmp_path):
    root = tmp_path / ".burnless"
    _seed(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "epochs:\n  restore_budget_tokens: 1000\n  startup_budget_tokens: 900\n",
        encoding="utf-8",
    )

    assert len(_ctx(_restore(root, source="clear"))) <= 1000 * 4
    assert len(_ctx(_restore(root, source="startup"))) <= 900 * 4


def test_explicit_budget_beats_config(tmp_path):
    root = tmp_path / ".burnless"
    _seed(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "epochs:\n  restore_budget_tokens: 1000\n", encoding="utf-8"
    )

    ctx = _ctx(_restore(root, source="clear", budget_tokens=3000))
    assert len(ctx) <= 3000 * 4
    assert len(ctx) > 1000 * 4


def test_cli_restore_without_flag_uses_config(tmp_path, capsys):
    root = tmp_path / ".burnless"
    _seed(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "epochs:\n  restore_budget_tokens: 1000\n", encoding="utf-8"
    )

    args = argparse.Namespace(
        epoch_cmd="restore",
        root=str(root),
        host="claude",
        host_session_id="sid-1",
        session_id=None,
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source="clear",
        budget_tokens=None,  # hook scripts no longer hardcode the flag
    )
    rc = cli.cmd_epoch(args)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert len(payload["hookSpecificOutput"]["additionalContext"]) <= 1000 * 4


def test_hook_templates_do_not_hardcode_budget():
    from pathlib import Path

    templates = Path(__file__).resolve().parents[1] / "templates" / "scripts"
    session = (templates / "burnless_epoch_session.sh").read_text(encoding="utf-8")
    seed = (templates / "burnless_session_seed.sh").read_text(encoding="utf-8")
    assert "--budget-tokens 2000" not in session
    assert '"1200"' not in seed
    # the explicit override path stays available
    assert "BURNLESS_RESTORE_BUDGET_TOKENS" in session
    assert "BURNLESS_STARTUP_BUDGET_TOKENS" in seed
