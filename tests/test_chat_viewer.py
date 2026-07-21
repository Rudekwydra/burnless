import json
from argparse import Namespace
from io import StringIO
from pathlib import Path

import pytest

from burnless import chat, cli


CHAIN_ID = "chain-1"
SESSION_1 = "session-one"
SESSION_2 = "session-two"

CODEX_CHAIN_ID = "chain-codex-1"
CODEX_SESSION_1 = "codex-session-one"


def _turn(role: str, text: str, ts: str) -> str:
    return json.dumps(
        {"timestamp": ts, "message": {"role": role, "content": text}},
        ensure_ascii=False,
    )


@pytest.fixture
def chat_artifacts(tmp_path: Path) -> dict[str, Path]:
    project_root = tmp_path / "project"
    burnless_root = project_root / ".burnless"
    chain_dir = burnless_root / "epochs" / "_rolling" / "chains" / CHAIN_ID
    chain_dir.mkdir(parents=True)
    (chain_dir / "chain.json").write_text(
        json.dumps(
            {
                "chain_id": CHAIN_ID,
                "host": "claude",
                "created": "2026-07-21T10:00:00Z",
                "last_seen": "2026-07-21T10:05:00Z",
                "sessions": [SESSION_1, SESSION_2],
            }
        ),
        encoding="utf-8",
    )

    sessions_root = burnless_root / "epochs" / "sessions" / "claude"
    for session_id, updated_at in (
        (SESSION_1, "2026-07-21T10:01:00Z"),
        (SESSION_2, "2026-07-21T10:03:00Z"),
    ):
        session_dir = sessions_root / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "checkpoint.json").write_text(
            json.dumps(
                {
                    "chain_id": CHAIN_ID,
                    "host_session_id": session_id,
                    "updated_at": updated_at,
                }
            ),
            encoding="utf-8",
        )

    projects_root = tmp_path / "claude-projects"
    transcript_dir = projects_root / "synthetic-project"
    transcript_dir.mkdir(parents=True)
    transcript_1 = transcript_dir / f"{SESSION_1}.jsonl"
    transcript_1.write_text(
        "\n".join(
            [
                _turn("user", "primeira pergunta", "2026-07-21T10:00:00Z"),
                "{linha corrompida",
                _turn("assistant", "primeira resposta", "2026-07-21T10:00:30Z"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    transcript_2 = transcript_dir / f"{SESSION_2}.jsonl"
    transcript_2.write_text(
        "\n".join(
            [
                _turn("user", "segunda pergunta", "2026-07-21T10:03:00Z"),
                _turn("assistant", "segunda resposta", "2026-07-21T10:03:30Z"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "project_root": project_root,
        "projects_root": projects_root,
        "transcript_1": transcript_1,
        "transcript_2": transcript_2,
    }


@pytest.fixture
def chat_artifacts_codex(tmp_path: Path) -> dict[str, Path]:
    project_root = tmp_path / "project"
    burnless_root = project_root / ".burnless"
    chain_dir = burnless_root / "epochs" / "_rolling" / "chains" / CODEX_CHAIN_ID
    chain_dir.mkdir(parents=True)
    (chain_dir / "chain.json").write_text(
        json.dumps(
            {
                "chain_id": CODEX_CHAIN_ID,
                "host": "codex",
                "created": "2026-07-21T10:00:00Z",
                "last_seen": "2026-07-21T10:01:00Z",
                "sessions": [CODEX_SESSION_1],
            }
        ),
        encoding="utf-8",
    )

    sessions_root = burnless_root / "epochs" / "sessions" / "codex"
    session_dir = sessions_root / CODEX_SESSION_1
    session_dir.mkdir(parents=True)
    (session_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "chain_id": CODEX_CHAIN_ID,
                "host_session_id": CODEX_SESSION_1,
                "updated_at": "2026-07-21T10:00:30Z",
            }
        ),
        encoding="utf-8",
    )

    projects_root = tmp_path / "codex-projects"
    transcript_dir = projects_root / "synthetic-project"
    transcript_dir.mkdir(parents=True)
    transcript = transcript_dir / f"{CODEX_SESSION_1}.jsonl"
    transcript.write_text(
        "\n".join(
            [
                _turn("user", "pergunta codex", "2026-07-21T10:00:00Z"),
                _turn("assistant", "resposta codex", "2026-07-21T10:00:30Z"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "project_root": project_root,
        "projects_root": projects_root,
        "transcript": transcript,
    }


def test_stitches_sessions_in_order_with_one_rollover_and_skips_corrupt_line(chat_artifacts):
    events = list(
        chat.stitch_events(
            chat_artifacts["project_root"],
            CHAIN_ID,
            projects_root=chat_artifacts["projects_root"],
        )
    )

    turns = [event for event in events if event["kind"] == "turn"]
    assert [event["text"] for event in turns] == [
        "primeira pergunta",
        "primeira resposta",
        "segunda pergunta",
        "segunda resposta",
    ]
    assert [event["session_id"] for event in turns] == [
        SESSION_1,
        SESSION_1,
        SESSION_2,
        SESSION_2,
    ]
    boundaries = [event for event in events if event["kind"] == "boundary"]
    assert len(boundaries) == 1
    assert boundaries[0]["session_id"] == SESSION_2


def test_json_mode_emits_valid_jsonl_contract(chat_artifacts, monkeypatch, capsys):
    monkeypatch.setattr(chat, "resolve_chat_root", lambda _cwd: chat_artifacts["project_root"])
    original_find = chat.find_transcript
    monkeypatch.setattr(
        chat,
        "find_transcript",
        lambda session_id, projects_root=None: original_find(
            session_id, projects_root=chat_artifacts["projects_root"]
        ),
    )

    rc = chat.main(
        Namespace(chain=CHAIN_ID, list=False, follow=False, json=True, verbose=False, cwd=None)
    )

    assert rc == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 4
    assert all(
        set(row) == {"chain_id", "session_id", "seq", "role", "ts", "text"}
        for row in rows
    )
    assert [row["seq"] for row in rows] == [1, 2, 3, 4]
    assert all(row["chain_id"] == CHAIN_ID for row in rows)


def test_missing_second_transcript_renders_notice_without_crash(chat_artifacts):
    chat_artifacts["transcript_2"].unlink()
    events = list(
        chat.stitch_events(
            chat_artifacts["project_root"],
            CHAIN_ID,
            projects_root=chat_artifacts["projects_root"],
        )
    )
    missing = [event for event in events if event["kind"] == "missing"]
    assert missing == [{"kind": "missing", "session_id": SESSION_2}]

    output = StringIO()
    for event in events:
        chat.emit_event(event, output)
    assert "(transcript não encontrado: session-)" in output.getvalue()


def test_current_follow_target_selects_newest_available_transcript(tmp_path):
    older = tmp_path / "older.jsonl"
    newer = tmp_path / "newer.jsonl"
    sessions = [{"session_id": SESSION_1}, {"session_id": SESSION_2}]

    assert chat.current_follow_target(
        sessions,
        {SESSION_1: older, SESSION_2: newer},
    ) == newer
    assert chat.current_follow_target(sessions, {SESSION_1: older}) == older


def test_incremental_reader_retries_a_partial_trailing_line(tmp_path):
    path = tmp_path / "live.jsonl"
    complete = _turn("user", "turno completo", "2026-07-21T10:00:00Z") + "\n"
    partial = _turn("assistant", "turno parcial", "2026-07-21T10:00:30Z")
    path.write_text(complete + partial, encoding="utf-8")

    offset, turns = chat._read_new_turns(path, 0)

    assert offset == len(complete.encode("utf-8"))
    assert [turn["text"] for turn in turns] == ["turno completo"]

    path.write_text(complete + partial + "\n", encoding="utf-8")
    final_offset, retried = chat._read_new_turns(path, offset)
    assert final_offset == path.stat().st_size
    assert [turn["text"] for turn in retried] == ["turno parcial"]


def test_cli_chat_subcommand_is_reachable(monkeypatch):
    observed = {}

    def fake_chat_main(args):
        observed.update(vars(args))
        return 0

    monkeypatch.setattr(cli.chat_mod, "main", fake_chat_main)

    assert cli.main(["chat", "--list"]) == 0
    assert observed["cmd"] == "chat"
    assert observed["list"] is True
    assert observed["chain"] is None
    assert observed["follow"] is False
    assert observed["json"] is False
    assert observed["verbose"] is False
    assert observed["host"] == "claude"


def test_chat_default_host_is_claude(chat_artifacts, monkeypatch, capsys):
    monkeypatch.setattr(chat, "resolve_chat_root", lambda _cwd: chat_artifacts["project_root"])
    original_find = chat.find_transcript
    monkeypatch.setattr(
        chat,
        "find_transcript",
        lambda session_id, projects_root=None: original_find(
            session_id, projects_root=chat_artifacts["projects_root"]
        ),
    )

    # No `host` attribute at all on args — main() must fall back to HOST ("claude").
    rc = chat.main(
        Namespace(chain=CHAIN_ID, list=False, follow=False, json=True, verbose=False, cwd=None)
    )

    assert rc == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["text"] for row in rows] == [
        "primeira pergunta",
        "primeira resposta",
        "segunda pergunta",
        "segunda resposta",
    ]


def test_chat_explicit_host_codex(chat_artifacts_codex, monkeypatch, capsys):
    monkeypatch.setattr(chat, "resolve_chat_root", lambda _cwd: chat_artifacts_codex["project_root"])
    original_find = chat.find_transcript
    monkeypatch.setattr(
        chat,
        "find_transcript",
        lambda session_id, projects_root=None: original_find(
            session_id, projects_root=chat_artifacts_codex["projects_root"]
        ),
    )

    rc = chat.main(
        Namespace(
            host="codex",
            chain=CODEX_CHAIN_ID,
            list=False,
            follow=False,
            json=True,
            verbose=False,
            cwd=None,
        )
    )

    assert rc == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["text"] for row in rows] == ["pergunta codex", "resposta codex"]


def test_chat_host_flag_wired_in_cli(monkeypatch):
    observed = {}

    def fake_chat_main(args):
        observed.update(vars(args))
        return 0

    monkeypatch.setattr(cli.chat_mod, "main", fake_chat_main)

    assert cli.main(["chat", "--host", "codex"]) == 0
    assert observed["host"] == "codex"


def test_chat_follow_skips_find_transcript_for_non_claude_host(chat_artifacts_codex, monkeypatch):
    monkeypatch.setattr(chat, "resolve_chat_root", lambda _cwd: chat_artifacts_codex["project_root"])

    find_transcript_calls = []
    original_find = chat.find_transcript

    def spy_find_transcript(session_id, projects_root=None):
        find_transcript_calls.append(session_id)
        return original_find(session_id, projects_root=chat_artifacts_codex["projects_root"])

    monkeypatch.setattr(chat, "find_transcript", spy_find_transcript)

    captured_offsets = {}

    def fake_follow(root, chain_id, host, as_json, verbose, seq_start, initial_offsets):
        captured_offsets.update(initial_offsets)
        return 0

    monkeypatch.setattr(chat, "_follow", fake_follow)

    rc = chat.main(
        Namespace(
            host="codex",
            chain=CODEX_CHAIN_ID,
            list=False,
            follow=True,
            json=True,
            verbose=False,
            cwd=None,
        )
    )

    assert rc == 0
    # Only the initial render (stitch_events) calls find_transcript — the
    # --follow byte-offset optimization must be skipped for non-claude hosts.
    assert find_transcript_calls == [CODEX_SESSION_1]
    assert captured_offsets == {}
