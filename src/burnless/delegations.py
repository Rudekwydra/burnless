from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone


DELEGATION_TEMPLATE = """\
# Delegation {id}

- **created_at:** {ts}
- **agent:** {agent_name} ({tier})
- **routed_by:** {routed_by}
- **status:** pending

## Goal

{goal}

## Task

{task}

## Constraints

- Be concise. No preamble.
- Output a final JSON block matching the success schema below; nothing else after it.
- Do not include logs in the JSON. Logs go to stdout, JSON last.
- Include `evidence`: short, verifiable items citing commands, files, logs, or checks observed. Evidence must not be opinion.

## Success criteria

{success}

## Report kind

{kind_hint}

## Required final output (last lines of stdout)

```json
{{
  "id": "{id}",
  "status": "OK | PART | ERR | BLK",
  "kind": "execution | thought",
  "summary": "<one short sentence>",
  "files_touched": [],
  "validated": [],
  "evidence": ["<command/file/log/check observed>"],
  "issues": [],
  "next": "<short hint or empty string>"
}}
```
"""


def render_delegation(
    *,
    delegation_id: str,
    goal: str,
    task: str,
    success: str,
    kind_hint: str,
    agent_name: str,
    tier: str,
    routed_by: str,
) -> str:
    return DELEGATION_TEMPLATE.format(
        id=delegation_id,
        ts=datetime.now(timezone.utc).isoformat(),
        goal=goal,
        task=task,
        success=success,
        kind_hint=kind_hint,
        agent_name=agent_name,
        tier=tier,
        routed_by=routed_by or "default-bronze",
    )


def extract_result_json(stdout: str) -> dict | None:
    """Find the last fenced ```json block in stdout and parse it. Best-effort."""
    if not stdout:
        return None
    marker = "```json"
    end_marker = "```"
    last_open = stdout.rfind(marker)
    if last_open == -1:
        # try a bare top-level json object at the end
        return _try_trailing_json(stdout)
    rest = stdout[last_open + len(marker):]
    close = rest.find(end_marker)
    payload = rest[:close] if close != -1 else rest
    try:
        return json.loads(payload.strip())
    except json.JSONDecodeError:
        return _try_trailing_json(stdout)


def _try_trailing_json(stdout: str) -> dict | None:
    s = stdout.strip()
    if not s.endswith("}"):
        return None
    # walk backward to matching open brace
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        c = s[i]
        if c == "}":
            depth += 1
        elif c == "{":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[i:])
                except json.JSONDecodeError:
                    return None
    return None


def write_delegation(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_log(path: Path, run_result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"# agent: {run_result.get('agent')}\n"
        f"# command: {' '.join(run_result.get('command', []))}\n"
        f"# kind: {run_result.get('kind')}\n"
        f"# returncode: {run_result.get('returncode')}\n"
        f"# duration_s: {run_result.get('duration_s')}\n"
        f"# started_at: {run_result.get('started_at')}\n"
        f"# ended_at: {run_result.get('ended_at')}\n"
        "\n--- STDOUT ---\n"
        f"{run_result.get('stdout', '')}\n"
        "\n--- STDERR ---\n"
        f"{run_result.get('stderr', '')}\n"
    )
    path.write_text(body, encoding="utf-8")


def write_summary(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
