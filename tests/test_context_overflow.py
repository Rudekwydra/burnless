from __future__ import annotations

import json
from pathlib import Path

from burnless import live_runner


def _mk_result(*, stdout: str, stderr: str = "", returncode: int = 1) -> live_runner.RunResult:
    return live_runner.RunResult(
        agent="haiku",
        command=["claude"],
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        started_at="2026-05-09T22:00:00+00:00",
        ended_at="2026-05-09T22:00:01+00:00",
        duration_s=1.0,
    )


def _conversation_prompt(turns: int = 8) -> str:
    parts = [
        "You are the assistant inside the Burnless shell for project 'demo'.",
        "Be concise. Match the user's language. No preamble.",
        "\n[recent conversation]",
    ]
    for i in range(turns):
        parts.append(f"user: msg{i}")
        parts.append(f"assistant: resp{i}")
    parts.append("\n[new message]")
    parts.append("user: latest")
    parts.append("assistant:")
    return "\n".join(parts)


def test_truncate_prompt_history_keeps_last_five_turns():
    prompt = _conversation_prompt(turns=8)

    truncated = live_runner.truncate_prompt_history(prompt, keep_turns=5)

    assert "user: msg0" not in truncated
    assert "assistant: resp2" not in truncated
    assert "user: msg3" in truncated
    assert "assistant: resp7" in truncated
    assert "user: latest" in truncated


def test_overflow_retry_truncates_then_escalates(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "overflow.log"
    prompt = _conversation_prompt(turns=8)
    calls: list[tuple[str, str, bool, str | None]] = []

    ok_json = {
        "id": "d217",
        "status": "OK",
        "kind": "execution",
        "summary": "done",
        "files_touched": [],
        "validated": [],
        "evidence": ["check: success"],
        "issues": [],
        "next": "",
    }

    def fake_run_with_live_panel(**kwargs):
        calls.append(
            (
                kwargs["tier"],
                kwargs["prompt"],
                bool(kwargs.get("append_log")),
                kwargs.get("append_label"),
            )
        )
        if len(calls) < 3:
            return _mk_result(stdout="Prompt is too long", stderr="context_length_exceeded")
        return _mk_result(stdout=f"```json\n{json.dumps(ok_json)}\n```", returncode=0)

    monkeypatch.setattr(live_runner, "run_with_live_panel", fake_run_with_live_panel)

    result = live_runner.run_with_overflow_retries(
        delegation_id="d217",
        tier="bronze",
        agent_cfg={"name": "haiku", "command": "haiku -p"},
        prompt=prompt,
        log_path=log_path,
        mode="plain",
        cwd=tmp_path,
        tier_agents={
            "bronze": {"name": "haiku", "command": "haiku -p"},
            "silver": {"name": "sonnet", "command": "sonnet -p"},
            "gold": {"name": "opus", "command": "opus -p"},
        },
    )

    assert len(calls) == 3
    assert calls[0][0] == "bronze"
    assert calls[1][0] == "bronze"
    assert calls[2][0] == "silver"
    assert "user: msg0" in calls[0][1]
    assert "user: msg0" not in calls[1][1]
    assert calls[1][1] == calls[2][1]
    assert calls[1][2] is True
    assert calls[1][3] == "OVERFLOW_RETRY_1 truncate-history tier=bronze"
    assert calls[2][3] == "OVERFLOW_RETRY_2 escalate tier=bronze->silver"
    assert json.loads(result.stdout.split("```json\n", 1)[1].rsplit("\n```", 1)[0])["status"] == "OK"


def test_overflow_retry_stops_after_three_total_attempts(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "overflow.log"
    prompt = _conversation_prompt(turns=8)
    calls: list[str] = []

    def fake_run_with_live_panel(**kwargs):
        calls.append(kwargs["tier"])
        return _mk_result(stdout="Prompt is too long", stderr="max_tokens exceeded")

    monkeypatch.setattr(live_runner, "run_with_live_panel", fake_run_with_live_panel)

    result = live_runner.run_with_overflow_retries(
        delegation_id="d217",
        tier="bronze",
        agent_cfg={"name": "haiku", "command": "haiku -p"},
        prompt=prompt,
        log_path=log_path,
        mode="plain",
        cwd=tmp_path,
        tier_agents={
            "bronze": {"name": "haiku", "command": "haiku -p"},
            "silver": {"name": "sonnet", "command": "sonnet -p"},
            "gold": {"name": "opus", "command": "opus -p"},
        },
    )

    payload = json.loads(result.stdout.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
    assert calls == ["bronze", "bronze", "silver"]
    assert payload["status"] == "ERR"
    assert "context_overflow_retry_exhausted" in payload["issues"]
