# /burnless-delegate

Delegate a task via Burnless Maestro from inside Claude Code.

## What this does

Routes your task to the right tier (gold/silver/bronze/diamond) based on routing keywords in `.burnless/config.yaml`, spawns the worker, streams output to a live panel, and writes a capsule to the Brain history.

## Usage

```
/burnless-delegate <task description>
```

## Steps

1. Check that Burnless is installed: run `burnless --version`. If not found, tell the user to run `pip install burnless` and stop.

2. Check that `.burnless/config.yaml` exists in the current directory. If not, tell the user to run `burnless init` first and stop.

3. Run: `burnless delegate "$ARGUMENTS"` and show the output.

4. If the delegation was created (dNNN), immediately run: `burnless run dNNN` and stream the output.

5. After completion, run `burnless status` and show the capsule summary.

## Notes

- Workers shell out to whatever CLI is configured in `config.yaml` — Claude, Codex, GPT-4o, Gemini, Ollama, anything.
- Raw logs are written to `.burnless/logs/dNNN.log` — never replayed into context.
- This skill itself is the Brain. The worker is what gets delegated.
