import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent / "templates" / "codex" / "hooks"


def _write_jsonl(path: Path, lines) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def _codex_uuid7(year: int, month: int, day: int, hour: int = 12) -> str:
    import datetime

    dt = datetime.datetime(year, month, day, hour, 0, 0, tzinfo=datetime.timezone.utc)
    ms = int(dt.timestamp() * 1000)
    ts_hex = format(ms, "012x")
    rest = "7abc9def012345678901"
    hex32 = ts_hex + rest
    assert len(hex32) == 32
    return f"{hex32[0:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:32]}"


def _run_hook(script: str, stdin_payload: dict, home: Path, timeout: int = 20) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        ["bash", str(HOOKS_DIR / script)],
        input=json.dumps(stdin_payload),
        cwd=str(HOOKS_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _dump_files(home: Path) -> list[Path]:
    dump_dir = home / ".burnless" / "codex_hook_payloads"
    if not dump_dir.exists():
        return []
    return list(dump_dir.glob("*.json"))


def test_stop_hook_missing_sid_dumps_and_exits_zero(tmp_path):
    result = _run_hook("burnless_epoch_stop.sh", {}, tmp_path)

    assert result.returncode == 0, result.stderr
    dumps = _dump_files(tmp_path)
    assert len(dumps) == 1, f"expected exactly one dump file, found {dumps}"


def test_stop_hook_unresolvable_sid_dumps_and_exits_zero(tmp_path):
    fake_sid = str(uuid.uuid4())
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)

    result = _run_hook(
        "burnless_epoch_stop.sh",
        {"session_id": fake_sid, "cwd": str(project_dir)},
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    dumps = _dump_files(tmp_path)
    assert len(dumps) == 1, f"expected exactly one dump file, found {dumps}"


def test_stop_hook_valid_sid_calls_extract_exchange(tmp_path):
    sid = _codex_uuid7(2026, 7, 21)
    day_dir = tmp_path / ".codex" / "sessions" / "2026" / "07" / "21"
    transcript = day_dir / f"rollout-2026-07-21T12-00-00-{sid}.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "session_meta", "payload": {"session_id": sid}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Real question one"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Real reply one"}],
                },
            },
        ],
    )

    project_dir = tmp_path / "proj"
    (project_dir / ".burnless").mkdir(parents=True)
    (project_dir / ".burnless" / "config.yaml").write_text("project: test\n", encoding="utf-8")

    result = _run_hook(
        "burnless_epoch_stop.sh",
        {"session_id": sid, "cwd": str(project_dir)},
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert not _dump_files(tmp_path), "valid sid must not fall back to payload dump"

    journal_files = list((project_dir / ".burnless" / "epochs" / "sessions").rglob("*.json"))
    assert journal_files, "expected a journal record under .burnless/epochs/sessions/**"
    combined = "\n".join(f.read_text(encoding="utf-8") for f in journal_files)
    assert "Real question one" in combined
    assert "Real reply one" in combined


def test_session_hook_missing_sid_exits_zero_no_crash(tmp_path):
    result = _run_hook("burnless_epoch_session.sh", {}, tmp_path)

    assert result.returncode == 0, result.stderr


def test_hooks_json_matches_real_plugin_schema_shape():
    data = json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))

    hooks = data["hooks"]
    assert "Stop" in hooks
    assert "SessionStart" in hooks

    for event in ("Stop", "SessionStart"):
        entries = hooks[event]
        assert isinstance(entries, list) and entries
        found_command = False
        for entry in entries:
            for inner in entry.get("hooks", []):
                if inner.get("type") == "command":
                    assert inner.get("command")
                    found_command = True
        assert found_command, f"no command hook found for {event}"


def test_codex_host_pid_matches_codex_process_name():
    content = (HOOKS_DIR / "codex_payload.sh").read_text(encoding="utf-8")
    assert "codex_host_pid()" in content
    assert "codex*)" in content
