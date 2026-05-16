from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

from .codec.decoder import normalize_worker_envelope


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
  "density": {{"efficiency": 0.5, "creativity": 0.5, "out_of_box": 0.5}},
  "salience": 0.5,
  "issues": [],
  "next": "<short hint or empty string>"
}}
```
"""


# Maestro chat — used for the persistent brain worker that the user talks
# to directly in the burnless shell. No JSON schema, no evidence contract:
# the worker should answer like a colleague in the same chat thread, in
# the user's language, using tools when needed and explaining what it did.
# Only sub-delegations (which the Maestro spawns via `burnless do --tier X`)
# need the JSON schema.
MAESTRO_CHAT_TEMPLATE = """\
# Conversa {id} (Maestro)

- **created_at:** {ts}
- **agent:** {agent_name} ({tier}) — Maestro
- **session:** persistent (via --resume)

## Mensagem do usuário

{task}

## Como responder

- Você é o Maestro Burnless: o worker principal que conversa direto com o
  usuário no shell. Mantém continuidade da conversa (turns anteriores
  estão no contexto via session resume).
- Responda em português natural, como colega no chat. Sem schema JSON,
  sem campo "evidence", sem `{{"status":...}}`.
- Use suas ferramentas (Read, Edit, Write, Bash, Glob, Grep) sempre que
  precisar olhar/mexer em arquivos. Diga o que fez.
- Se a tarefa pede execução pesada ou paralela, você pode delegar para
  sub-workers stateless via Bash:
    `burnless do --tier silver "tarefa específica"` (silver/sonnet)
    `burnless do --tier gold "decisão arquitetural"` (gold/opus)
    `burnless do --tier bronze "leitura/classificação"` (bronze/haiku)
  Sub-delegações não compartilham seu histórico — passe contexto no prompt.
- Termine com a próxima ação clara ou pergunta objetiva. Sem JSON ao final.
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


def render_maestro_chat(
    *,
    delegation_id: str,
    task: str,
    agent_name: str,
    tier: str,
) -> str:
    return MAESTRO_CHAT_TEMPLATE.format(
        id=delegation_id,
        ts=datetime.now(timezone.utc).isoformat(),
        task=task,
        agent_name=agent_name,
        tier=tier,
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
        return normalize_worker_envelope(json.loads(payload.strip()))
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
                    return normalize_worker_envelope(json.loads(s[i:]))
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
