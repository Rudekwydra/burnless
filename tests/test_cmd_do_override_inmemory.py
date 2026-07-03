"""Regression tests for 2026-07-02 audit findings #2 and #10.

#2  (critical): `burnless do --gold codex:gpt-5.5 ...` documented the
    --diamond/--gold/--silver/--bronze override as "this run only", but the
    old implementation patched .burnless/config.yaml on disk and restored it
    in a `finally` — a crash, SIGKILL, or a parallel run reading the file
    mid-patch could see/keep the override permanently.
#10 (minor): the delegation markdown was rendered via cmd_delegate() BEFORE
    the override was applied, so its "- **Agent:**" line showed the
    pre-override agent even though the run itself used the override.

Fix: overrides are now applied in-memory only (RunOpts.worker_overrides /
cmd_delegate's cfg_override), never written to disk.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from burnless import cli, config as config_mod, paths as paths_mod


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / ".burnless"
    p = paths_mod.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
        p[key].mkdir(parents=True, exist_ok=True)
    config_mod.write_default(
        p["config"],
        agents_override={
            "gold": {
                "name": "claude-opus",
                "command": "claude -p --model opus --permission-mode bypassPermissions",
                "provider": "anthropic",
            }
        },
    )
    return root


def _do_args(**overrides) -> argparse.Namespace:
    base = dict(
        text="Investigate the reported issue and summarize findings.",
        tier="gold",
        force=True,
        allow_relative_paths=True,
        allow_unfenced_verify=True,
        timeout=600,
        stale_timeout_s=None,
        cold_cache=False,
        diamond=None,
        gold=None,
        silver=None,
        bronze=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestWorkerOverrideNeverPersistsToDisk:
    def test_gold_override_does_not_touch_config_yaml_on_disk(self, tmp_path, monkeypatch):
        root = _make_project(tmp_path)
        p = paths_mod.paths_for(root)
        monkeypatch.setattr(cli.paths_mod, "require_root", lambda: root)

        original_config_bytes = p["config"].read_bytes()

        from burnless.exec import runner as rmod

        monkeypatch.setattr(rmod.agents_mod, "is_available", lambda cfg: True)

        captured_calls = []

        class _FakeRunResult:
            def to_dict(self_inner):
                return {
                    "agent": "gpt-5.5",
                    "command": ["codex", "exec", "-m", "gpt-5.5"],
                    "stdout": (
                        '```json\n{"id": "d001", "status": "OK", "kind": "execution", '
                        '"summary": "done", "files_touched": [], "validated": [], '
                        '"evidence": ["ok"], "issues": [], "next": ""}\n```'
                    ),
                    "stderr": "",
                    "returncode": 0,
                    "started_at": "2026-07-02T00:00:00Z",
                    "ended_at": "2026-07-02T00:00:01Z",
                    "duration_s": 1.0,
                    "interrupted": False,
                    "stale": False,
                }

        def fake_run_with_overflow_retries(**kwargs):
            captured_calls.append(kwargs)
            kwargs["log_path"].parent.mkdir(parents=True, exist_ok=True)
            kwargs["log_path"].write_text("", encoding="utf-8")
            return _FakeRunResult()

        monkeypatch.setattr(rmod.live_runner, "run_with_overflow_retries", fake_run_with_overflow_retries)

        args = _do_args(gold="codex:gpt-5.5")
        rc = cli.cmd_do(args)

        assert rc == 0
        # Config on disk must be byte-identical before and after — the
        # override never touched the file (the core of finding #2).
        assert p["config"].read_bytes() == original_config_bytes

        # But the run itself DID use the override.
        assert len(captured_calls) == 1
        used_agent_cfg = captured_calls[0]["agent_cfg"]
        assert used_agent_cfg["provider"] == "codex"
        assert "-m gpt-5.5" in used_agent_cfg["command"]

        # The delegation markdown reflects the override too (finding #10),
        # not the pre-override "claude-opus".
        did = getattr(args, "_allocated_did", None) or "d001"
        deleg_text = (p["delegations"] / f"{did}.md").read_text(encoding="utf-8")
        assert "claude-opus" not in deleg_text
        assert "gpt-5.5" in deleg_text

    def test_no_override_leaves_config_untouched_and_uses_default_agent(self, tmp_path, monkeypatch):
        """Sanity check: the no-override path still behaves as before."""
        root = _make_project(tmp_path)
        p = paths_mod.paths_for(root)
        monkeypatch.setattr(cli.paths_mod, "require_root", lambda: root)

        original_config_bytes = p["config"].read_bytes()

        from burnless.exec import runner as rmod

        monkeypatch.setattr(rmod.agents_mod, "is_available", lambda cfg: True)

        captured_calls = []

        class _FakeRunResult:
            def to_dict(self_inner):
                return {
                    "agent": "opus",
                    "command": ["claude", "-p", "--model", "opus"],
                    "stdout": (
                        '```json\n{"id": "d002", "status": "OK", "kind": "execution", '
                        '"summary": "done", "files_touched": [], "validated": [], '
                        '"evidence": ["ok"], "issues": [], "next": ""}\n```'
                    ),
                    "stderr": "",
                    "returncode": 0,
                    "started_at": "2026-07-02T00:00:00Z",
                    "ended_at": "2026-07-02T00:00:01Z",
                    "duration_s": 1.0,
                    "interrupted": False,
                    "stale": False,
                }

        def fake_run_with_overflow_retries(**kwargs):
            captured_calls.append(kwargs)
            kwargs["log_path"].parent.mkdir(parents=True, exist_ok=True)
            kwargs["log_path"].write_text("", encoding="utf-8")
            return _FakeRunResult()

        monkeypatch.setattr(rmod.live_runner, "run_with_overflow_retries", fake_run_with_overflow_retries)

        args = _do_args()
        rc = cli.cmd_do(args)

        assert rc == 0
        assert p["config"].read_bytes() == original_config_bytes
        assert captured_calls[0]["agent_cfg"]["provider"] == "anthropic"
