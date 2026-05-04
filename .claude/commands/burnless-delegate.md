# /burnless-delegate

Delegate a task through the Burnless protocol from inside Claude Code.

Routes to the right tier (gold/silver/bronze) via keywords in `.burnless/config.yaml`,
executes the worker, and writes a capsule to session history.

## Usage

```
/burnless-delegate <task description>
```

## Steps

1. Check burnless is installed: `burnless --version`. If missing, tell user `pip install burnless` and stop.

2. Check `.burnless/config.yaml` exists. If missing, tell user `burnless init` and stop.

3. Run:
   ```
   burnless delegate "$ARGUMENTS"
   ```
   Note the delegation ID (e.g. `d042`).

4. Immediately run:
   ```
   burnless run d042
   ```
   Stream the output.

5. After completion show: `burnless status`

## Tiers

| Tier | Default agent | Use for |
|------|--------------|---------|
| gold | claude-opus-4-7 | Architecture, strategy, complex reasoning |
| silver | claude-sonnet-4-6 | Implementation, docs, code |
| bronze | claude-haiku-4-5 | Summarize, classify, extract |

Tier is chosen automatically. Override: `burnless delegate --tier gold "<task>"`

## Notes

- This Claude Code session IS the Brain. Workers are what get delegated.
- Raw logs in `.burnless/logs/dNNN.log` — never replayed into context.
- Worker output is compressed into a capsule for the next turn.
