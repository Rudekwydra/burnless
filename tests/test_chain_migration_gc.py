from __future__ import annotations

import argparse
import json
import os
import subprocess
import time

from burnless import cli
from burnless import config as config_mod
from burnless import recovery


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _write_legacy_pool_handoff(root, sid, *, pid, claimed_by=None, cwd=None):
    handoff_dir = root / "epochs" / "_rolling" / "handoffs"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "host": "claude",
        "host_session_id": sid,
        "old_sid": sid,
        "process_instance_id": pid,
        "cwd": cwd,
        "claimed_by": claimed_by,
        "claimed_at": None,
        "created_at": "2026-06-01T00:00:00Z",
    }
    (handoff_dir / f"{sid}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_migration_creates_chain_for_unclaimed_legacy_handoff(tmp_path):
    root = tmp_path / ".burnless"
    _write_legacy_pool_handoff(root, "old-sid-1", pid="proc-legacy-1", cwd="/tmp/proj")

    result = recovery.migrate_legacy_handoff_pool(root, host="claude")

    assert result["migrated"] == ["old-sid-1.json"]
    chain_id = recovery._find_chain_id_by_pid(root, "claude", "proc-legacy-1")
    assert chain_id is not None
    chain_handoff = json.loads((root / "epochs" / "_rolling" / "chains" / chain_id / "handoff.json").read_text(encoding="utf-8"))
    assert chain_handoff["old_sid"] == "old-sid-1"


def test_migration_archives_already_claimed_legacy_handoff_without_chain(tmp_path):
    root = tmp_path / ".burnless"
    _write_legacy_pool_handoff(root, "old-sid-2", pid="proc-legacy-2", claimed_by="some-newer-sid")

    result = recovery.migrate_legacy_handoff_pool(root, host="claude")

    assert result["archived"] == ["old-sid-2.json"]
    assert recovery._find_chain_id_by_pid(root, "claude", "proc-legacy-2") is None
    assert (root / "epochs" / "_rolling" / "handoffs" / "_migrated" / "old-sid-2.json").exists()


def test_migration_is_idempotent(tmp_path):
    root = tmp_path / ".burnless"
    _write_legacy_pool_handoff(root, "old-sid-3", pid="proc-legacy-3")

    first = recovery.migrate_legacy_handoff_pool(root, host="claude")
    second = recovery.migrate_legacy_handoff_pool(root, host="claude")

    assert first["migrated"] == ["old-sid-3.json"]
    assert second["migrated"] == []
    assert second["skipped"] == ["old-sid-3.json"]


def test_gc_archives_dead_chain_older_than_7_days_and_exports(tmp_path):
    root = tmp_path / ".burnless"
    dead = _dead_pid()
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="gc-sid",
        process_instance_id=str(dead),
        living_md="## Foco atual\n- trabalho a ser exportado no GC\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )
    recovery.write_handoff(root, host="claude", host_session_id="gc-sid", process_instance_id=str(dead))
    chain_id = recovery._find_chain_id_by_pid(root, "claude", str(dead))
    assert chain_id is not None
    meta_path = root / "epochs" / "_rolling" / "chains" / chain_id / "chain.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    old_last_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 86400))
    meta["last_seen"] = old_last_seen
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    result = recovery.gc_dead_chains(root, host="claude")

    assert result["archived"] == [chain_id]
    assert not (root / "epochs" / "_rolling" / "chains" / chain_id).exists()
    assert (root / "epochs" / "_rolling" / "chains" / "_archived" / chain_id / "chain.json").exists()
    exports_dir = root / "exports"
    assert exports_dir.exists() and any(exports_dir.glob("*.md"))


def test_gc_ignores_live_chain_regardless_of_age(tmp_path):
    import os

    root = tmp_path / ".burnless"
    recovery.write_handoff(root, host="claude", host_session_id="live-sid", process_instance_id=str(os.getpid()))
    chain_id = recovery._find_chain_id_by_pid(root, "claude", str(os.getpid()))
    meta_path = root / "epochs" / "_rolling" / "chains" / chain_id / "chain.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30 * 86400))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    result = recovery.gc_dead_chains(root, host="claude")

    assert result["archived"] == []
    assert (root / "epochs" / "_rolling" / "chains" / chain_id).exists()


class TestChainStateClassifier:
    def test_unresolvable_pid_is_unknown_not_alive(self):
        meta = {
            "pid": "550e8400-e29b-41d4-a716-446655440000",
            "pid_proc_name": "",
            "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        # Regression proof: today's `alive` field reads True via `not _pid_is_dead(...)`
        # for this exact meta, but `state` must say "unknown", not "active".
        assert recovery._pid_is_dead(meta["pid"]) is False
        assert recovery.classify_chain_state(meta) == "unknown"

    def test_missing_pid_is_unknown(self):
        meta = {"last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        assert recovery.classify_chain_state(meta) == "unknown"

    def test_dead_pid_is_dead(self):
        dead = _dead_pid()
        meta = {
            "pid": str(dead),
            "pid_proc_name": "",
            "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        assert recovery.classify_chain_state(meta) == "dead"

    def test_stale_when_last_seen_beyond_ttl(self):
        import os

        # time.mktime(time.strptime(...)) parses the (UTC-produced) last_seen string
        # as local time, so on a non-UTC host the recovered epoch is off by the local
        # UTC offset. Pad well past any real-world offset (max ~14h) so this assertion
        # is robust regardless of the machine's timezone.
        now = time.time()
        old_last_seen = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(now - recovery.CHAIN_STALE_TTL_SECONDS - 16 * 3600),
        )
        meta = {
            "pid": str(os.getpid()),
            "pid_proc_name": "",
            "last_seen": old_last_seen,
        }
        assert recovery.classify_chain_state(meta, now=now) == "stale"

    def test_active_when_recent_heartbeat(self):
        import os

        meta = {
            "pid": str(os.getpid()),
            "pid_proc_name": "",
            "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        assert recovery.classify_chain_state(meta) == "active"

    def test_list_chains_adds_state_key_without_changing_alive(self, tmp_path):
        import os

        root = tmp_path / ".burnless"
        recovery.write_handoff(
            root, host="claude", host_session_id="alive-sid", process_instance_id=str(os.getpid())
        )
        recovery.write_handoff(
            root,
            host="claude",
            host_session_id="uuid-sid",
            process_instance_id="550e8400-e29b-41d4-a716-446655440000",
        )

        chains = recovery.list_chains(root, host="claude")

        assert len(chains) == 2
        for entry in chains:
            assert "alive" in entry
            assert "state" in entry

        uuid_entry = next(c for c in chains if c["pid"] == "550e8400-e29b-41d4-a716-446655440000")
        assert uuid_entry["state"] == "unknown"
        naive_state = "active" if uuid_entry["alive"] else "dead"
        assert uuid_entry["state"] != naive_state


class TestGcDryRun:
    def _make_archivable_chain(self, tmp_path):
        root = tmp_path / ".burnless"
        dead = _dead_pid()
        recovery.write_checkpoint(
            root,
            host="claude",
            host_session_id="dry-gc-sid",
            process_instance_id=str(dead),
            living_md="## Foco atual\n- trabalho a ser exportado no GC\n",
            harvested_state={"contracts": [], "refs": [], "open_threads": []},
            applied_through=0,
        )
        recovery.write_handoff(root, host="claude", host_session_id="dry-gc-sid", process_instance_id=str(dead))
        chain_id = recovery._find_chain_id_by_pid(root, "claude", str(dead))
        meta_path = root / "epochs" / "_rolling" / "chains" / chain_id / "chain.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        old_last_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 86400))
        meta["last_seen"] = old_last_seen
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return root, chain_id

    def test_gc_dry_run_reports_without_writing(self, tmp_path):
        root, chain_id = self._make_archivable_chain(tmp_path)

        result = recovery.gc_dead_chains(root, host="claude", dry_run=True)

        assert result == {"would_archive": [chain_id]}
        chain_dir = root / "epochs" / "_rolling" / "chains" / chain_id
        assert chain_dir.exists()
        archived_root = root / "epochs" / "_rolling" / "chains" / "_archived"
        assert not archived_root.exists() or not any(archived_root.iterdir())

    def test_gc_dry_run_is_idempotent(self, tmp_path):
        root, chain_id = self._make_archivable_chain(tmp_path)

        first = recovery.gc_dead_chains(root, host="claude", dry_run=True)
        second = recovery.gc_dead_chains(root, host="claude", dry_run=True)

        assert first == second == {"would_archive": [chain_id]}

    def test_gc_real_run_still_unchanged(self, tmp_path):
        root, chain_id = self._make_archivable_chain(tmp_path)

        result = recovery.gc_dead_chains(root, host="claude")

        assert result == {"archived": [chain_id]}
        assert not (root / "epochs" / "_rolling" / "chains" / chain_id).exists()
        assert (root / "epochs" / "_rolling" / "chains" / "_archived" / chain_id / "chain.json").exists()


def _init_cli_project(tmp_path):
    burnless = tmp_path / ".burnless"
    for d in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
        (burnless / d).mkdir(parents=True, exist_ok=True)
    config_mod.write_default(burnless / "config.yaml")
    return burnless


def _status_args(**overrides):
    defaults = dict(show_all_chains=False)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _gc_args(**overrides):
    defaults = dict(dry_run=False, host="claude")
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestCmdStatusChainFiltering:
    def _seed_active_and_dead_chain(self, root):
        recovery.write_handoff(
            root, host="claude", host_session_id="active-sid", process_instance_id=str(os.getpid())
        )
        dead = _dead_pid()
        recovery.write_handoff(
            root, host="claude", host_session_id="dead-sid", process_instance_id=str(dead)
        )
        active_chain_id = recovery._find_chain_id_by_pid(root, "claude", str(os.getpid()))
        dead_chain_id = recovery._find_chain_id_by_pid(root, "claude", str(dead))
        return active_chain_id, dead_chain_id

    def test_status_default_shows_only_active(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        root = _init_cli_project(tmp_path)
        active_chain_id, dead_chain_id = self._seed_active_and_dead_chain(root)

        rc = cli.cmd_status(_status_args(show_all_chains=False))

        assert rc == 0
        out = capsys.readouterr().out
        assert active_chain_id in out
        assert dead_chain_id not in out
        assert "hidden" in out
        assert "--all" in out

    def test_status_all_shows_everything(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        root = _init_cli_project(tmp_path)
        active_chain_id, dead_chain_id = self._seed_active_and_dead_chain(root)

        rc = cli.cmd_status(_status_args(show_all_chains=True))

        assert rc == 0
        out = capsys.readouterr().out
        assert active_chain_id in out
        assert dead_chain_id in out
        assert "active" in out
        assert "dead" in out

    def test_status_all_only_active_chains_matches_default(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        root = _init_cli_project(tmp_path)
        recovery.write_handoff(
            root, host="claude", host_session_id="only-active-sid", process_instance_id=str(os.getpid())
        )
        active_chain_id = recovery._find_chain_id_by_pid(root, "claude", str(os.getpid()))

        rc_default = cli.cmd_status(_status_args(show_all_chains=False))
        out_default = capsys.readouterr().out
        rc_all = cli.cmd_status(_status_args(show_all_chains=True))
        out_all = capsys.readouterr().out

        assert rc_default == 0
        assert rc_all == 0
        assert active_chain_id in out_default
        assert active_chain_id in out_all
        assert "hidden" not in out_default
        assert "hidden" not in out_all


class TestCmdGc:
    def _make_archivable_chain(self, tmp_path):
        root = tmp_path / ".burnless"
        dead = _dead_pid()
        recovery.write_checkpoint(
            root,
            host="claude",
            host_session_id="cli-gc-sid",
            process_instance_id=str(dead),
            living_md="## Foco atual\n- trabalho a ser exportado no GC\n",
            harvested_state={"contracts": [], "refs": [], "open_threads": []},
            applied_through=0,
        )
        recovery.write_handoff(root, host="claude", host_session_id="cli-gc-sid", process_instance_id=str(dead))
        chain_id = recovery._find_chain_id_by_pid(root, "claude", str(dead))
        meta_path = root / "epochs" / "_rolling" / "chains" / chain_id / "chain.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        old_last_seen = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 86400))
        meta["last_seen"] = old_last_seen
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return root, chain_id

    def test_gc_dry_run_cli_reports_json(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _init_cli_project(tmp_path)
        root, chain_id = self._make_archivable_chain(tmp_path)

        rc = cli.cmd_gc(_gc_args(dry_run=True))

        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert chain_id in payload["would_archive"]
        chain_dir = root / "epochs" / "_rolling" / "chains" / chain_id
        assert chain_dir.exists()

    def test_gc_real_run_cli_archives(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _init_cli_project(tmp_path)
        root, chain_id = self._make_archivable_chain(tmp_path)

        rc = cli.cmd_gc(_gc_args(dry_run=False))

        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert chain_id in payload["archived"]
        chain_dir = root / "epochs" / "_rolling" / "chains" / chain_id
        assert not chain_dir.exists()
        assert (root / "epochs" / "_rolling" / "chains" / "_archived" / chain_id / "chain.json").exists()
