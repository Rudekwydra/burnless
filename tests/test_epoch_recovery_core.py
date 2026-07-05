from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path

import pytest
import subprocess


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _write_transcript(path: Path, entries: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def _transcript_entries() -> list[dict]:
    return [
        {
            "type": "user",
            "uuid": "u-001",
            "message": {
                "id": "msg-user-001",
                "role": "user",
                "content": "primeira pergunta",
            },
        },
        {
            "type": "assistant",
            "uuid": "a-001",
            "message": {
                "id": "msg-assistant-001",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-001",
                        "name": "edit",
                        "input": {"file_path": "/tmp/app/src/main.py"},
                    },
                    {"type": "text", "text": "primeira resposta"},
                ],
            },
        },
        {
            "type": "user",
            "uuid": "u-002",
            "message": {
                "id": "msg-user-002",
                "role": "user",
                "content": "<task-notification> ignora isto",
            },
        },
    ]


def test_extract_exchange_prefers_last_real_pair_and_files(tmp_path):
    from burnless import recovery

    transcript = _write_transcript(tmp_path / "transcript.jsonl", _transcript_entries())

    envelope = recovery.extract_exchange(
        transcript,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        cwd="/tmp/app",
        source="clear",
    )

    assert envelope["host"] == "claude"
    assert envelope["host_session_id"] == "sid-1"
    assert envelope["process_instance_id"] == "proc-1"
    assert envelope["user_text"] == "primeira pergunta"
    assert envelope["assistant_text"] == "primeira resposta"
    assert "/tmp/app/src/main.py" in envelope["files"]
    assert envelope["exchange_id"].startswith("sha256:")
    assert "<task-notification>" not in envelope["user_text"]


def test_journal_append_allocates_unique_seq_and_dedupes(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    env1 = {
        "schema": 1,
        "host": "claude",
        "host_session_id": "sid-1",
        "process_instance_id": "proc-1",
        "transcript_path": "/tmp/transcript-a.jsonl",
        "exchange_id": "sha256:one",
        "user_text": "u1",
        "assistant_text": "a1",
        "files": [],
    }
    env2 = dict(env1, exchange_id="sha256:two", user_text="u2", assistant_text="a2")

    results: list[dict] = []

    def worker(envelope: dict) -> None:
        results.append(recovery.journal_append(root, envelope))

    t1 = threading.Thread(target=worker, args=(env1,))
    t2 = threading.Thread(target=worker, args=(env2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    seqs = sorted(r["seq"] for r in results)
    assert seqs == [1, 2]

    deduped = recovery.journal_append(root, env1)
    by_exchange = {r["exchange_id"]: r["seq"] for r in results}
    assert deduped["seq"] == by_exchange[env1["exchange_id"]]

    journal_files = sorted((root / "epochs" / "sessions" / "claude" / "sid-1" / "journal").glob("*.json"))
    assert len(journal_files) == 2


def test_compaction_failure_preserves_checkpoint_and_restore_delta(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    env = {
        "schema": 1,
        "host": "claude",
        "host_session_id": "sid-1",
        "process_instance_id": "proc-1",
        "transcript_path": "/tmp/transcript-a.jsonl",
        "exchange_id": "sha256:one",
        "user_text": "pergunta pendente",
        "assistant_text": "resposta pendente",
        "files": ["/tmp/app/src/main.py"],
    }
    recovery.journal_append(root, env)
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    result = recovery.compact_pending(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        rewriter=lambda _prompt: None,
    )
    assert result["status"] == "failed"

    checkpoint = recovery.read_checkpoint(root, "claude", "sid-1")
    assert checkpoint["applied_through"] == 0

    payload = recovery.render_restore(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source="clear",
    )
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    meta = payload["recovery"]
    assert "objetivo vivo" in ctx
    assert "pergunta pendente" in ctx
    assert "resposta pendente" in ctx
    assert meta["pending_count"] == 1
    assert meta["journal_head"] == 1
    assert meta["applied_through"] == 0
    assert meta["checkpoint_chars"] > 0


def test_restore_does_not_repeat_applied_delta(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    env = {
        "schema": 1,
        "host": "claude",
        "host_session_id": "sid-1",
        "process_instance_id": "proc-1",
        "transcript_path": "/tmp/transcript-a.jsonl",
        "exchange_id": "sha256:one",
        "user_text": "pergunta",
        "assistant_text": "resposta",
        "files": [],
    }
    record = recovery.journal_append(root, env)
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- consolidado\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=record["seq"],
    )

    payload = recovery.render_restore(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        new_session_id="sid-2",
        source="clear",
    )
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "## Trocas ainda não consolidadas" not in ctx
    assert ctx.count("consolidado") == 1


def test_handoffs_do_not_cross_between_process_instances(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    env_a = {
        "schema": 1,
        "host": "claude",
        "host_session_id": "sid-a",
        "process_instance_id": "proc-a",
        "transcript_path": "/tmp/transcript-a.jsonl",
        "exchange_id": "sha256:a",
        "user_text": "pergunta a",
        "assistant_text": "resposta a",
        "files": [],
    }
    env_b = dict(env_a, host_session_id="sid-b", process_instance_id="proc-b", exchange_id="sha256:b")
    recovery.journal_append(root, env_a)
    recovery.journal_append(root, env_b)
    recovery.write_handoff(root, host="claude", host_session_id="sid-a", process_instance_id="proc-a")
    recovery.write_handoff(root, host="claude", host_session_id="sid-b", process_instance_id="proc-b")

    claimed_a = recovery.claim_handoff(root, host="claude", process_instance_id="proc-a", new_session_id="sid-a2")
    claimed_b = recovery.claim_handoff(root, host="claude", process_instance_id="proc-b", new_session_id="sid-b2")

    assert claimed_a["old_sid"] == "sid-a"
    assert claimed_b["old_sid"] == "sid-b"
    assert claimed_a["claimed_by"] == "sid-a2"
    assert claimed_b["claimed_by"] == "sid-b2"


def test_legacy_claude_paths_are_readable(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    legacy_dir = root / "epochs" / "legacy-sid"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "generation": 1,
                "host": "claude",
                "host_session_id": "legacy-sid",
                "process_instance_id": "legacy-proc",
                "living_md": "## Foco atual\n- legacy\n",
                "harvested_state": {"contracts": [], "refs": [], "open_threads": []},
                "applied_through": 0,
                "updated_at": "2026-07-02T00:00:00Z",
                "content_hash": "sha256:deadbeef",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    checkpoint = recovery.read_checkpoint(root, "claude", "legacy-sid")
    assert checkpoint["host"] == "claude"
    assert checkpoint["host_session_id"] == "legacy-sid"
    assert "legacy" in checkpoint["living_md"]


def test_epoch_session_hook_restores_clear_handoff(tmp_path):
    script = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "burnless_epoch_session.sh"
    home = tmp_path / "home"
    project = home / "antigravity" / "demo"
    project.mkdir(parents=True)
    (project / ".burnless").mkdir(parents=True)
    (project / ".burnless" / "config.yaml").write_text("epochs:\n  enabled: true\n", encoding="utf-8")

    root = project / ".burnless"
    session_root = root / "epochs" / "sessions" / "claude" / "old-sid"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "checkpoint.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "generation": 1,
                "host": "claude",
                "host_session_id": "old-sid",
                "process_instance_id": "proc-1",
                "living_md": "## Foco atual\n- objetivo vivo\n",
                "harvested_state": {"contracts": [], "refs": [], "open_threads": []},
                "applied_through": 0,
                "journal_head": 1,
                "updated_at": "2026-07-02T00:00:00Z",
                "content_hash": "sha256:deadbeef",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    journal_dir = session_root / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / "000001-sha256-one.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "seq": 1,
                "host": "claude",
                "host_session_id": "old-sid",
                "process_instance_id": "proc-1",
                "exchange_id": "sha256:one",
                "user_text": "ultima pergunta",
                "assistant_text": "ultima resposta",
                "files": ["/tmp/app/src/main.py"],
                "transcript_path": "/tmp/transcript.jsonl",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    handoff_dir = root / "epochs" / "_rolling" / "handoffs"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    (handoff_dir / "old-sid.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "host": "claude",
                "host_session_id": "old-sid",
                "old_sid": "old-sid",
                "process_instance_id": "proc-1",
                "root": str(root),
                "journal_head": 1,
                "claimed_by": None,
                "claimed_at": None,
                "created_at": "2026-07-02T00:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["BURNLESS_WORKSPACE_ROOT"] = str(home / "antigravity")
    proc = subprocess.run(
        [
            "bash",
            str(script),
        ],
        input=json.dumps(
            {
                "session_id": "new-sid",
                "cwd": str(project),
                "source": "clear",
                "process_instance_id": "proc-1",
            }
        ),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), proc.stderr
    payload = json.loads(proc.stdout)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "objetivo vivo" in ctx
    assert "ultima pergunta" in ctx
    assert "ultima resposta" in ctx


def _seed_session_state(root: Path, *, pid: str = "old-sid") -> None:
    session_root = root / "epochs" / "sessions" / "claude" / "old-sid"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "checkpoint.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "generation": 1,
                "host": "claude",
                "host_session_id": "old-sid",
                "process_instance_id": pid,
                "living_md": "## Foco atual\n- objetivo vivo\n",
                "harvested_state": {"contracts": [], "refs": [], "open_threads": []},
                "applied_through": 0,
                "journal_head": 1,
                "updated_at": "2026-07-02T00:00:00Z",
                "content_hash": "sha256:deadbeef",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    journal_dir = session_root / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / "000001-sha256-one.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "seq": 1,
                "host": "claude",
                "host_session_id": "old-sid",
                "process_instance_id": pid,
                "exchange_id": "sha256:one",
                "user_text": "ultima pergunta",
                "assistant_text": "ultima resposta",
                "files": ["/tmp/app/src/main.py"],
                "transcript_path": "/tmp/transcript.jsonl",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_claim_handoff_ttl_fallback_without_stable_pid(tmp_path):
    """Real Claude Code hooks carry no process_instance_id: SessionEnd falls
    back to the OLD sid and SessionStart to the NEW sid. The claim must still
    succeed for a fresh unclaimed handoff of the same project (RM-4C.4)."""
    from burnless import recovery

    root = tmp_path / ".burnless"
    _seed_session_state(root)
    recovery.write_handoff(root, host="claude", host_session_id="old-sid", process_instance_id="old-sid")

    claimed = recovery.claim_handoff(root, host="claude", process_instance_id="new-sid", new_session_id="new-sid")
    assert claimed is not None
    assert claimed["old_sid"] == "old-sid"
    assert claimed["claimed_by"] == "new-sid"
    assert claimed["claim_mode"] == "ttl_fallback"


def test_claim_handoff_ttl_fallback_ignores_stale(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    _seed_session_state(root)
    recovery.write_handoff(root, host="claude", host_session_id="old-sid", process_instance_id="old-sid")
    handoff_path = root / "epochs" / "_rolling" / "handoffs" / "old-sid.json"
    stale = os.path.getmtime(handoff_path) - 600
    os.utime(handoff_path, (stale, stale))

    claimed = recovery.claim_handoff(root, host="claude", process_instance_id="new-sid", new_session_id="new-sid")
    assert claimed is None


def test_claim_handoff_prefers_pid_match_over_fresh_foreign(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    _seed_session_state(root, pid="proc-a")
    recovery.write_handoff(root, host="claude", host_session_id="old-sid", process_instance_id="proc-a")
    recovery.write_handoff(root, host="claude", host_session_id="other-sid", process_instance_id="proc-b")

    claimed = recovery.claim_handoff(root, host="claude", process_instance_id="proc-a", new_session_id="sid-a2")
    assert claimed is not None
    assert claimed["old_sid"] == "old-sid"
    assert claimed["claim_mode"] == "pid"


def test_epoch_session_hook_restores_with_realistic_claude_payload(tmp_path):
    """End-to-end with the payload shapes Claude Code actually sends: no
    process_instance_id anywhere, SessionEnd sid != SessionStart sid."""
    script = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "burnless_epoch_session.sh"
    home = tmp_path / "home"
    project = home / "antigravity" / "demo"
    project.mkdir(parents=True)
    root = project / ".burnless"
    root.mkdir(parents=True)
    (root / "config.yaml").write_text("epochs:\n  enabled: true\n", encoding="utf-8")
    _seed_session_state(root)

    from burnless import recovery

    recovery.write_handoff(root, host="claude", host_session_id="old-sid", process_instance_id="old-sid")

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["BURNLESS_WORKSPACE_ROOT"] = str(home / "antigravity")
    proc = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({"session_id": "new-sid", "cwd": str(project), "source": "clear"}),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "restore must be served without a stable process_instance_id"
    payload = json.loads(proc.stdout)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "objetivo vivo" in ctx
    assert "ultima pergunta" in ctx


def test_epoch_end_hook_accepts_reason_clear(tmp_path):
    """SessionEnd payloads carry `reason`, not `source`; the end hook must
    still write journal + handoff."""
    script = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "burnless_epoch_end.sh"
    home = tmp_path / "home"
    project = home / "antigravity" / "demo"
    project.mkdir(parents=True)
    root = project / ".burnless"
    root.mkdir(parents=True)
    (root / "config.yaml").write_text("epochs:\n  enabled: true\n", encoding="utf-8")

    transcript = _write_transcript(project / "transcript.jsonl", _transcript_entries())

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["BURNLESS_WORKSPACE_ROOT"] = str(home / "antigravity")
    proc = subprocess.run(
        ["bash", str(script)],
        input=json.dumps(
            {
                "session_id": "old-sid",
                "cwd": str(project),
                "transcript_path": str(transcript),
                "reason": "clear",
            }
        ),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    handoff_dir = root / "epochs" / "_rolling" / "handoffs"
    assert list(handoff_dir.glob("*.json")), "SessionEnd(reason=clear) must write a handoff"
    journal_dir = root / "epochs" / "sessions" / "claude" / "old-sid" / "journal"
    assert list(journal_dir.glob("*.json")), "SessionEnd(reason=clear) must journal the last exchange"


def test_inherit_checkpoint_bootstraps_new_session(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    _seed_session_state(root)

    committed = recovery.inherit_checkpoint(
        root, host="claude", new_session_id="new-sid", process_instance_id="proc-x", old_session_id="old-sid"
    )
    assert committed is not None
    fresh = recovery.read_checkpoint(root, "claude", "new-sid")
    assert fresh is not None
    assert "objetivo vivo" in fresh["living_md"]
    assert int(fresh["applied_through"]) == 0
    assert int(fresh["journal_head"]) == 0


def test_inherit_checkpoint_is_idempotent(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    _seed_session_state(root)
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="new-sid",
        process_instance_id="proc-x",
        living_md="## Foco atual\n- doc proprio da sessao nova\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=3,
        journal_head=3,
    )

    assert recovery.inherit_checkpoint(
        root, host="claude", new_session_id="new-sid", process_instance_id="proc-x", old_session_id="old-sid"
    ) is None
    kept = recovery.read_checkpoint(root, "claude", "new-sid")
    assert "doc proprio" in kept["living_md"]
    assert int(kept["applied_through"]) == 3


def test_inherit_checkpoint_falls_back_to_latest_project_checkpoint(tmp_path):
    from burnless import recovery

    root = tmp_path / ".burnless"
    _seed_session_state(root)

    committed = recovery.inherit_checkpoint(
        root, host="claude", new_session_id="new-sid", process_instance_id="proc-x", old_session_id=None
    )
    assert committed is not None
    assert "objetivo vivo" in recovery.read_checkpoint(root, "claude", "new-sid")["living_md"]


def test_clear_restore_inherits_checkpoint_for_new_session(tmp_path):
    """The full /clear hook path must leave the NEW session with an inherited
    checkpoint so its compaction evolves the living doc (memoria eterna)."""
    script = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "burnless_epoch_session.sh"
    home = tmp_path / "home"
    project = home / "antigravity" / "demo"
    project.mkdir(parents=True)
    root = project / ".burnless"
    root.mkdir(parents=True)
    (root / "config.yaml").write_text("epochs:\n  enabled: true\n", encoding="utf-8")
    _seed_session_state(root)

    from burnless import recovery

    recovery.write_handoff(root, host="claude", host_session_id="old-sid", process_instance_id="old-sid")

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["BURNLESS_WORKSPACE_ROOT"] = str(home / "antigravity")
    proc = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({"session_id": "new-sid", "cwd": str(project), "source": "clear"}),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), proc.stderr

    inherited = recovery.read_checkpoint(root, "claude", "new-sid")
    assert inherited is not None, "new session must inherit the predecessor checkpoint"
    assert "objetivo vivo" in inherited["living_md"]
    assert int(inherited["applied_through"]) == 0


def test_inherited_doc_evolves_on_compaction(tmp_path):
    """After inheritance, compaction must fold new exchanges INTO the
    inherited doc (prompt carries it) instead of starting from scratch."""
    from burnless import recovery

    root = tmp_path / ".burnless"
    _seed_session_state(root)
    recovery.inherit_checkpoint(
        root, host="claude", new_session_id="new-sid", process_instance_id="proc-x", old_session_id="old-sid"
    )

    recovery.journal_append(
        root,
        {
            "schema": 1,
            "host": "claude",
            "host_session_id": "new-sid",
            "process_instance_id": "proc-x",
            "exchange_id": "sha256:nova-troca",
            "user_text": "nova pergunta",
            "assistant_text": "nova resposta",
            "files": [],
            "transcript_path": "/tmp/t.jsonl",
        },
    )

    prompts: list[str] = []

    def rewriter(prompt: str) -> str:
        prompts.append(prompt)
        return prompt.split("## Trocas pendentes")[0].strip() + "\n- evoluido com nova troca\n"

    result = recovery.compact_pending(
        root, host="claude", host_session_id="new-sid", process_instance_id="proc-x", rewriter=rewriter
    )
    assert result["status"] == "committed", result
    assert "objetivo vivo" in prompts[0], "compaction prompt must carry the inherited doc"
    final = recovery.read_checkpoint(root, "claude", "new-sid")
    assert "objetivo vivo" in final["living_md"]
    assert "evoluido com nova troca" in final["living_md"]
    assert int(final["applied_through"]) == 1


def test_pending_seed_writes_respect_state_dir_override(tmp_path, monkeypatch):
    """Pilot rollover must never write the operator's real ~/.burnless/state:
    running the test suite was contaminating live sessions (audit 2026-07-03)."""
    from burnless.pilot.rollover import _pending_seed_path, _write_pending_seed

    state_dir = tmp_path / "state"
    monkeypatch.setenv("BURNLESS_STATE_DIR", str(state_dir))

    _write_pending_seed(tmp_path / "proj", "conteudo do restore")
    written = _pending_seed_path()
    assert written == state_dir / "pending_seed.md"
    assert written.exists()
    assert "conteudo do restore" in written.read_text(encoding="utf-8")


def test_foreign_pending_seed_does_not_silence_startup_restore(tmp_path):
    """A pending_seed targeted at ANOTHER project must not suppress this
    project's startup restore (it used to sys.exit(0) on target mismatch)."""
    script = Path(__file__).resolve().parents[1] / "templates" / "scripts" / "burnless_session_seed.sh"
    home = tmp_path / "home"
    project = home / "antigravity" / "demo"
    project.mkdir(parents=True)
    root = project / ".burnless"
    root.mkdir(parents=True)
    (root / "config.yaml").write_text("epochs:\n  enabled: true\n", encoding="utf-8")
    _seed_session_state(root)

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    foreign = state_dir / "pending_seed.md"
    foreign.write_text(
        "<!-- burnless-seed-target: /outro/projeto/qualquer -->\nseed de outro projeto\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["BURNLESS_WORKSPACE_ROOT"] = str(home / "antigravity")
    env["BURNLESS_STATE_DIR"] = str(state_dir)
    proc = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({"session_id": "startup-sid", "cwd": str(project), "source": "startup"}),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip(), "startup restore must still be served despite a foreign pending_seed"
    assert "objetivo vivo" in proc.stdout
    assert "seed de outro projeto" not in proc.stdout
    assert foreign.exists(), "foreign seed must be left intact for its owner project"


def test_compact_prompt_has_task_framing():
    """F1: the compactor prompt must carry an explicit task/framing instead of
    being a bare document (the root cause of chat-completion hallucination)."""
    from burnless import recovery

    checkpoint = {"living_md": "## Foco atual\n- objetivo vivo\n"}
    pending = [
        {
            "seq": 1,
            "exchange_id": "sha256:one",
            "user_text": "pergunta real",
            "assistant_text": "resposta real",
            "files": [],
        }
    ]

    prompt = recovery._build_compact_prompt(checkpoint, pending)

    assert "NUNCA" in prompt
    assert "RESUMO PRÉVIO" in prompt
    assert "PROIBIDO" in prompt


def test_compact_rejects_chat_completion(tmp_path):
    """F3: a rewriter that answers like a chat assistant must be rejected
    fail-closed — checkpoint stays untouched, applied_through does not move."""
    from burnless import recovery

    root = tmp_path / ".burnless"
    env = {
        "schema": 1,
        "host": "claude",
        "host_session_id": "sid-1",
        "process_instance_id": "proc-1",
        "transcript_path": "/tmp/transcript-a.jsonl",
        "exchange_id": "sha256:one",
        "user_text": "pergunta pendente",
        "assistant_text": "resposta pendente",
        "files": [],
    }
    recovery.journal_append(root, env)
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    before = recovery.read_checkpoint(root, "claude", "sid-1")

    result = recovery.compact_pending(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        rewriter=lambda _prompt: "RESPOSTA:\nSim, claro, tudo certo.",
    )

    assert result["status"] == "rejected"

    after = recovery.read_checkpoint(root, "claude", "sid-1")
    assert after["living_md"] == before["living_md"]
    assert after["applied_through"] == 0


def test_compact_rejects_phantom_seq(tmp_path):
    """F3: a candidate referencing a seq number absent from pending/prev_md
    must be rejected (anti-seq-fantasma check)."""
    from burnless import recovery

    root = tmp_path / ".burnless"
    env = {
        "schema": 1,
        "host": "claude",
        "host_session_id": "sid-1",
        "process_instance_id": "proc-1",
        "transcript_path": "/tmp/transcript-a.jsonl",
        "exchange_id": "sha256:one",
        "user_text": "pergunta pendente",
        "assistant_text": "resposta pendente",
        "files": [],
    }
    recovery.journal_append(root, env)
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo vivo\n",
        harvested_state={"contracts": [], "refs": [], "open_threads": []},
        applied_through=0,
    )

    result = recovery.compact_pending(
        root,
        host="claude",
        host_session_id="sid-1",
        process_instance_id="proc-1",
        rewriter=lambda _prompt: "## Foco atual\n- seq 9999 testou X\n",
    )

    assert result["status"] == "rejected"


def test_ollama_payload_has_system(tmp_path, monkeypatch):
    """F2: the ollama-local /api/generate payload must carry a `system` field
    (defense in depth against the completion-only endpoint)."""
    from burnless import epochs_v2

    project = tmp_path / "proj"
    (project / ".burnless").mkdir(parents=True)
    (project / ".burnless" / "config.yaml").write_text(
        "encoder:\n  provider: ollama-local\n  model: gemma\n",
        encoding="utf-8",
    )

    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["body"] = json.loads(req.data)
        return _FakeResponse(json.dumps({"response": "## Foco atual\n- ok\n"}).encode())

    monkeypatch.delenv("BURNLESS_LOCAL_API", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    epochs_v2.living_rewriter(project)("qualquer prompt")

    assert seen["body"].get("system")
