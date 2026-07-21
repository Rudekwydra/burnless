import json
from pathlib import Path

from burnless import recovery

# Fixture covers every branch of extract_exchange's ~100-line parsing logic
# (design doc docs/plans/2026-07-21-G2-transcript-sources-design.md, section 3):
# content-as-string, content-as-block-list, a tool_use block (type != "text"),
# an isSidechain=True line, a restore-noise line, a malformed JSON line, an
# empty line, and a truncated final line.
_FIXTURE_LINES = [
    {"type": "user", "uuid": "u-001", "message": {"id": "msg-u-001", "role": "user", "content": "primeira pergunta"}},
    {
        "type": "assistant",
        "uuid": "a-001",
        "message": {
            "id": "msg-a-001",
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "/tmp/app/src/main.py"}},
                {"type": "text", "text": "primeira resposta"},
            ],
        },
    },
    "",  # empty line
    "{not valid json",  # malformed JSON line
    {
        "type": "user",
        "uuid": "u-side",
        "isSidechain": True,
        "message": {"id": "msg-u-side", "role": "user", "content": "side channel question"},
    },
    {
        "type": "user",
        "uuid": "u-noise",
        "message": {"id": "msg-u-noise", "role": "user", "content": "[BURNLESS RESTORE] host=claude old_sid=x"},
    },
    {"type": "user", "uuid": "u-002", "message": {"id": "msg-u-002", "role": "user", "content": "segunda pergunta"}},
    {
        "type": "assistant",
        "uuid": "a-002",
        "message": {"id": "msg-a-002", "role": "assistant", "content": [{"type": "text", "text": "segunda resposta"}]},
    },
]

# Precomputed via a real (pre-existing-behavior) call to extract_exchange against
# this exact fixture, host="claude", host_session_id="s1", process_instance_id="p1",
# cwd="/tmp/golden", source="test" — with the non-deterministic "captured_at" and
# the tmp_path-dependent "transcript_path" fields stripped out before hashing.
_EXPECTED_SNAPSHOT = json.dumps(
    {
        "assistant_record": "a-002",
        "assistant_text": "segunda resposta",
        "cwd": "/tmp/golden",
        "empty": False,
        "exchange_id": "sha256:26915e5f15a6dbf8e29567e5571e1728286adf5857eef2f6e35d420fe0778ee1",
        "files": ["/tmp/app/src/main.py"],
        "host": "claude",
        "host_session_id": "s1",
        "process_instance_id": "p1",
        "schema": 1,
        "source": "test",
        "transcript_found": True,
        "user_record": "u-002",
        "user_text": "segunda pergunta",
    },
    sort_keys=True,
)


def _write_fixture(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for line in _FIXTURE_LINES:
            if isinstance(line, str):
                f.write(line + "\n")
            else:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        # Truncated-looking final line: no closing braces, no trailing newline.
        f.write('{"type": "user", "uuid": "u-trunc", "message": {"role": "user", "content": "trunca')
    return path


def test_extract_exchange_golden_snapshot(tmp_path):
    transcript = _write_fixture(tmp_path / "transcript.jsonl")

    envelope = recovery.extract_exchange(
        transcript,
        host="claude",
        host_session_id="s1",
        process_instance_id="p1",
        cwd="/tmp/golden",
        source="test",
    )

    assert envelope.pop("transcript_path") == str(transcript)
    assert envelope.pop("captured_at")

    assert json.dumps(envelope, sort_keys=True) == _EXPECTED_SNAPSHOT
